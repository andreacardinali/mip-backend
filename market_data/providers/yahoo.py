from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

import httpx

from http_client.retry import RetryPolicy, request_with_retry
from market_data.capabilities import InstrumentType, MarketDataCapability
from market_data.exceptions import (
    InstrumentNotFoundError,
    InvalidProviderResponseError,
    ProviderTemporaryError,
)
from market_data.models import MarketQuote
from market_data.providers.base import MarketDataProvider

YAHOO_CHART_BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"


class YahooProvider(MarketDataProvider):
    """Yahoo Finance provider for exact-listing live prices."""

    name = "yahoo"
    capabilities = {MarketDataCapability.LIVE_PRICE}
    supported_capabilities = capabilities
    instrument_types = set(InstrumentType)
    supported_instrument_types = instrument_types

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        retry_policy: RetryPolicy | None = None,
        client: httpx.Client | None = None,
    ):
        self.timeout = timeout
        self.retry_policy = retry_policy or RetryPolicy()
        self._client = client

    def supports(self, capability, instrument_type) -> bool:
        return (
            capability in self.capabilities and instrument_type in self.instrument_types
        )

    def get_quote(self, instrument) -> MarketQuote:
        if not instrument.symbol:
            raise InstrumentNotFoundError("Yahoo requires symbol")

        symbol = instrument.symbol
        owns_client = self._client is None
        client = self._client or httpx.Client(
            timeout=self.timeout,
            headers={"User-Agent": "Mozilla/5.0"},
        )

        try:
            response = request_with_retry(
                client,
                "GET",
                f"{YAHOO_CHART_BASE_URL}/{quote(symbol, safe='')}",
                policy=self.retry_policy,
                params={"range": "5d", "interval": "1d"},
            )
            return self._parse_response(response, symbol)
        except httpx.HTTPStatusError as error:
            raise _normalize_http_error(error, symbol) from error
        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
            httpx.RemoteProtocolError,
        ) as error:
            raise ProviderTemporaryError(f"Yahoo transport error: {error}") from error
        finally:
            if owns_client:
                client.close()

    @staticmethod
    def _parse_response(response: httpx.Response, symbol: str) -> MarketQuote:
        # Keep status handling here as well so direct parser tests and callers
        # that supply a response continue to receive normalized provider errors.
        if response.status_code == 404:
            raise InstrumentNotFoundError(symbol)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise _normalize_http_error(error, symbol) from error

        try:
            payload: dict[str, Any] = response.json()
        except ValueError as error:
            raise InvalidProviderResponseError("Yahoo returned invalid JSON") from error

        chart = payload.get("chart") if isinstance(payload, dict) else None
        error_payload = chart.get("error") if isinstance(chart, dict) else None
        results = chart.get("result") if isinstance(chart, dict) else None
        if error_payload:
            raise InstrumentNotFoundError(
                error_payload.get("description") or str(error_payload)
            )
        if not isinstance(results, list) or not results:
            raise InstrumentNotFoundError(symbol)

        result = results[0]
        meta = result.get("meta") if isinstance(result, dict) else None
        if not isinstance(meta, dict):
            raise InvalidProviderResponseError("Yahoo response has no meta")

        price = meta.get("regularMarketPrice")
        timestamp = meta.get("regularMarketTime")
        if price is None:
            price, timestamp = _latest_close(result)
        if price is None:
            raise InstrumentNotFoundError(
                f"Yahoo returned no market price for {symbol}"
            )

        return MarketQuote(
            timestamp=int(timestamp or time.time()),
            price=float(price),
            currency=meta.get("currency"),
            source="yahoo",
        )


def _normalize_http_error(
    error: httpx.HTTPStatusError,
    symbol: str,
) -> Exception:
    status_code = error.response.status_code
    if status_code == 404:
        return InstrumentNotFoundError(symbol)
    if status_code in {408, 425, 429} or status_code >= 500:
        return ProviderTemporaryError(f"Yahoo HTTP {status_code}")
    return InstrumentNotFoundError(symbol)


def _latest_close(result: dict[str, Any]) -> tuple[float | None, int | None]:
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    quote_rows = indicators.get("quote") or []
    closes = quote_rows[0].get("close") or [] if quote_rows else []
    for index in range(min(len(timestamps), len(closes)) - 1, -1, -1):
        if closes[index] is not None:
            return float(closes[index]), int(timestamps[index])
    return None, None
