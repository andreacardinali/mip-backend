from __future__ import annotations

from typing import Any
from urllib.parse import quote

from http_client import (
    RETRYABLE_EXCEPTIONS,
    HttpClient,
    HttpResponse,
    HttpStatusError,
    RetryPolicy,
    create_http_client,
    request_with_retry,
)
from market_data.capabilities import (
    InstrumentType,
    MarketDataCapability,
    ProviderCapabilities,
)
from market_data.exceptions import (
    InstrumentNotFoundError,
    InvalidProviderResponseError,
    ProviderTemporaryError,
)
from market_data.models import (
    HistoryRequest,
    InstrumentSearchResult,
    MarketQuote,
    PriceBar,
    PricePoint,
)

YAHOO_CHART_BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
YAHOO_SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
_ALL_INSTRUMENT_TYPES = frozenset(InstrumentType)

MarketPriceCandidate = tuple[float, int]


class YahooProvider:
    """Yahoo Finance provider for exact-listing quotes and price history."""

    name = "yahoo"
    capabilities = ProviderCapabilities(
        supported=frozenset(
            {
                MarketDataCapability.SYMBOL_SEARCH,
                MarketDataCapability.LIVE_PRICE,
                MarketDataCapability.PRICE_HISTORY,
                MarketDataCapability.OHLCV,
            }
        ),
        instrument_types={
            MarketDataCapability.SYMBOL_SEARCH: _ALL_INSTRUMENT_TYPES,
            MarketDataCapability.LIVE_PRICE: _ALL_INSTRUMENT_TYPES,
            MarketDataCapability.PRICE_HISTORY: _ALL_INSTRUMENT_TYPES,
            MarketDataCapability.OHLCV: _ALL_INSTRUMENT_TYPES,
        },
    )

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        retry_policy: RetryPolicy | None = None,
        client: HttpClient | None = None,
    ) -> None:
        self.timeout = timeout
        self.retry_policy = retry_policy or RetryPolicy()
        self._client = client

    def supports(
        self,
        capability,
        instrument_type=InstrumentType.UNKNOWN,
        interval=None,
    ) -> bool:
        """Compatibility facade over the shared capability descriptor."""
        return self.capabilities.supports(
            capability,
            instrument_type=instrument_type,
            interval=interval,
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
    ) -> list[InstrumentSearchResult]:
        clean_query = _normalize_search_query(query)
        if not clean_query or limit <= 0:
            return []

        payload = self._request_search(clean_query, limit=limit)
        quotes = payload.get("quotes") if isinstance(payload, dict) else None
        if not isinstance(quotes, list):
            raise InvalidProviderResponseError(
                "Yahoo search response has invalid quotes payload"
            )

        results = []
        seen = set()
        for row in quotes:
            if not isinstance(row, dict):
                continue

            symbol = str(row.get("symbol") or "").strip()
            if not symbol:
                continue

            key = symbol.upper()
            if key in seen:
                continue

            seen.add(key)
            results.append(
                InstrumentSearchResult(
                    symbol=symbol,
                    name=str(row.get("longname") or row.get("shortname") or symbol),
                    instrument_type=_instrument_type(row),
                    currency=row.get("currency") or None,
                    source=self.name,
                )
            )

            if len(results) >= limit:
                break

        return results

    def get_quote(self, instrument) -> MarketQuote:
        if not instrument.symbol:
            raise InstrumentNotFoundError("Yahoo requires symbol")

        response = self._request_chart(
            instrument.symbol,
            params={
                "range": "1mo",
                "interval": "1d",
                "includePrePost": "false",
                "events": "div,splits",
            },
        )
        return self._parse_response(response, instrument.symbol)

    def get_price_history(self, request: HistoryRequest) -> list[PricePoint]:
        return [
            PricePoint(
                timestamp=bar.timestamp,
                price=bar.close,
                currency=request.instrument.currency,
            )
            for bar in self.get_ohlcv(request)
        ]

    def get_ohlcv(self, request: HistoryRequest) -> list[PriceBar]:
        symbol = request.instrument.symbol
        if not symbol:
            raise InstrumentNotFoundError("Yahoo requires symbol")

        params: dict[str, Any] = {
            "range": request.period,
            "interval": request.interval,
            "events": "div,splits",
        }
        if request.from_timestamp is not None:
            params.pop("range", None)
            params["period1"] = request.from_timestamp
        if request.to_timestamp is not None:
            params.pop("range", None)
            params["period2"] = request.to_timestamp

        response = self._request_chart(symbol, params=params)
        return self._parse_ohlcv_response(response, symbol)

    def get_history(self, symbol: str, *, period: str, interval: str):
        """Compatibility entry point for remaining legacy callers."""
        from market_data.models import InstrumentReference

        return self.get_ohlcv(
            HistoryRequest(
                instrument=InstrumentReference(symbol=symbol),
                period=period,
                interval=interval,
            )
        )

    def _request_search(self, query: str, *, limit: int) -> dict[str, Any]:
        owns_client = self._client is None
        client = self._client or create_http_client(
            timeout=self.timeout,
            headers={"User-Agent": "Mozilla/5.0 mip-market-data"},
        )
        try:
            response = request_with_retry(
                client,
                "GET",
                YAHOO_SEARCH_URL,
                policy=self.retry_policy,
                params={
                    "q": query,
                    "quotesCount": max(limit, 1),
                    "newsCount": 0,
                },
            )
            try:
                payload = response.json()
            except ValueError as error:
                raise InvalidProviderResponseError(
                    "Yahoo search returned invalid JSON"
                ) from error

            if not isinstance(payload, dict):
                raise InvalidProviderResponseError(
                    "Yahoo search returned invalid payload"
                )

            return payload
        except HttpStatusError as error:
            raise _normalize_http_error(error, query) from error
        except RETRYABLE_EXCEPTIONS as error:
            raise ProviderTemporaryError(f"Yahoo transport error: {error}") from error
        finally:
            if owns_client:
                client.close()

    def _request_chart(
        self,
        symbol: str,
        *,
        params: dict[str, Any],
    ) -> HttpResponse:
        owns_client = self._client is None
        client = self._client or create_http_client(
            timeout=self.timeout,
            headers={"User-Agent": "Mozilla/5.0 mip-market-data"},
        )
        try:
            return request_with_retry(
                client,
                "GET",
                f"{YAHOO_CHART_BASE_URL}/{quote(symbol, safe='')}",
                policy=self.retry_policy,
                params=params,
            )
        except HttpStatusError as error:
            raise _normalize_http_error(error, symbol) from error
        except RETRYABLE_EXCEPTIONS as error:
            raise ProviderTemporaryError(f"Yahoo transport error: {error}") from error
        finally:
            if owns_client:
                client.close()

    @staticmethod
    def _parse_response(
        response: HttpResponse,
        symbol: str,
    ) -> MarketQuote:
        if response.status_code == 404:
            raise InstrumentNotFoundError(symbol)

        try:
            response.raise_for_status()
        except HttpStatusError as error:
            raise _normalize_http_error(error, symbol) from error

        try:
            payload: dict[str, Any] = response.json()
        except ValueError as error:
            raise InvalidProviderResponseError("Yahoo returned invalid JSON") from error

        return YahooProvider._parse_quote(payload, symbol)

    @staticmethod
    def _parse_quote(
        payload: dict[str, Any],
        symbol: str,
    ) -> MarketQuote:
        chart = payload.get("chart") if isinstance(payload, dict) else None
        if not isinstance(chart, dict):
            raise InvalidProviderResponseError("Yahoo response has no chart payload")

        error_payload = chart.get("error")
        if error_payload:
            if isinstance(error_payload, dict):
                description = (
                    error_payload.get("description")
                    or error_payload.get("code")
                    or str(error_payload)
                )
            else:
                description = str(error_payload)
            raise InstrumentNotFoundError(description)

        results = chart.get("result")
        if not isinstance(results, list) or not results:
            raise InstrumentNotFoundError(symbol)

        result = results[0]
        if not isinstance(result, dict):
            raise InvalidProviderResponseError("Yahoo returned an invalid chart result")

        meta = result.get("meta")
        if not isinstance(meta, dict):
            raise InvalidProviderResponseError("Yahoo response has no meta")

        meta_candidate = _market_price_candidate(
            price=meta.get("regularMarketPrice"),
            timestamp=meta.get("regularMarketTime"),
        )
        close_candidate = _latest_close(result)
        selected = _newest_market_price(
            meta_candidate,
            close_candidate,
        )

        if selected is None:
            raise InstrumentNotFoundError(
                f"Yahoo returned no market price for {symbol}"
            )

        price, timestamp = selected
        return MarketQuote(
            timestamp=timestamp,
            price=price,
            currency=_optional_string(meta.get("currency")),
            source="yahoo",
        )

    @staticmethod
    def _parse_ohlcv_response(
        response: HttpResponse,
        symbol: str,
    ) -> list[PriceBar]:
        result = _chart_result(response, symbol)
        timestamps = result.get("timestamp") or []
        indicators = result.get("indicators") or {}
        quote_rows = indicators.get("quote") or []
        values = quote_rows[0] if quote_rows and isinstance(quote_rows[0], dict) else {}

        bars = []
        for index, timestamp in enumerate(timestamps):
            row = {
                field: _value_at(values.get(field), index)
                for field in ("open", "high", "low", "close", "volume")
            }
            if any(row[field] is None for field in ("open", "high", "low", "close")):
                continue

            bars.append(
                PriceBar(
                    timestamp=int(timestamp),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=(
                        float(row["volume"]) if row["volume"] is not None else None
                    ),
                )
            )

        return bars


def _chart_result(
    response: HttpResponse,
    symbol: str,
) -> dict[str, Any]:
    if response.status_code == 404:
        raise InstrumentNotFoundError(symbol)

    try:
        response.raise_for_status()
    except HttpStatusError as error:
        raise _normalize_http_error(error, symbol) from error

    try:
        payload = response.json()
    except ValueError as error:
        raise InvalidProviderResponseError("Yahoo returned invalid JSON") from error

    chart = payload.get("chart") if isinstance(payload, dict) else None
    error_payload = chart.get("error") if isinstance(chart, dict) else None
    results = chart.get("result") if isinstance(chart, dict) else None

    if error_payload:
        if isinstance(error_payload, dict):
            description = (
                error_payload.get("description")
                or error_payload.get("code")
                or str(error_payload)
            )
        else:
            description = str(error_payload)
        raise InstrumentNotFoundError(description)

    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        raise InstrumentNotFoundError(symbol)

    return results[0]


def _normalize_http_error(
    error: HttpStatusError,
    symbol: str,
) -> Exception:
    status_code = error.response.status_code
    if status_code == 404:
        return InstrumentNotFoundError(symbol)
    if status_code in {408, 425, 429} or status_code >= 500:
        return ProviderTemporaryError(f"Yahoo HTTP {status_code}")
    return InstrumentNotFoundError(symbol)


def _market_price_candidate(
    *,
    price: Any,
    timestamp: Any,
) -> MarketPriceCandidate | None:
    if price is None or timestamp is None:
        return None

    try:
        normalized_price = float(price)
        normalized_timestamp = int(timestamp)
    except (TypeError, ValueError):
        return None

    if normalized_price < 0 or normalized_timestamp <= 0:
        return None

    return normalized_price, normalized_timestamp


def _newest_market_price(
    *candidates: MarketPriceCandidate | None,
) -> MarketPriceCandidate | None:
    valid_candidates = [candidate for candidate in candidates if candidate is not None]
    if not valid_candidates:
        return None

    return max(
        valid_candidates,
        key=lambda candidate: candidate[1],
    )


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None

    normalized = str(value).strip()
    return normalized or None


def _latest_close(
    result: dict[str, Any],
) -> MarketPriceCandidate | None:
    timestamps = result.get("timestamp")
    if not isinstance(timestamps, list):
        return None

    indicators = result.get("indicators")
    if not isinstance(indicators, dict):
        return None

    quote_rows = indicators.get("quote")
    if not isinstance(quote_rows, list) or not quote_rows:
        return None

    quote_row = quote_rows[0]
    if not isinstance(quote_row, dict):
        return None

    closes = quote_row.get("close")
    if not isinstance(closes, list):
        return None

    row_count = min(len(timestamps), len(closes))
    for index in range(row_count - 1, -1, -1):
        candidate = _market_price_candidate(
            price=closes[index],
            timestamp=timestamps[index],
        )
        if candidate is not None:
            return candidate

    return None


def _value_at(values, index):
    if not values or index >= len(values):
        return None
    return values[index]


def _normalize_search_query(value: str) -> str:
    return str(value or "").split(" - ", 1)[0].strip()


def _instrument_type(row: dict[str, Any]) -> InstrumentType:
    value = str(row.get("quoteType") or row.get("typeDisp") or "").upper()
    return {
        "EQUITY": InstrumentType.STOCK,
        "STOCK": InstrumentType.STOCK,
        "ETF": InstrumentType.ETF,
        "ETP": InstrumentType.ETP,
        "MUTUALFUND": InstrumentType.FUND,
        "FUND": InstrumentType.FUND,
        "INDEX": InstrumentType.INDEX,
        "CRYPTOCURRENCY": InstrumentType.CRYPTO,
        "CRYPTO": InstrumentType.CRYPTO,
        "FUTURE": InstrumentType.COMMODITY,
    }.get(value, InstrumentType.UNKNOWN)
