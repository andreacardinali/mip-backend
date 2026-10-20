from urllib.parse import quote

import httpx

from http_client.retry import RetryPolicy, request_with_retry
from market_data.capabilities import (
    InstrumentType,
    MarketDataCapability,
    ProviderCapabilities,
)
from market_data.exceptions import InstrumentNotFoundError
from market_data.models import (
    ExchangeReference,
    InstrumentProfile,
    InstrumentSearchResult,
    MarketQuote,
    PriceBar,
    PricePoint,
)

_PROFILE_TYPES = frozenset(
    {
        InstrumentType.STOCK,
        InstrumentType.ETF,
        InstrumentType.ETP,
        InstrumentType.UNKNOWN,
    }
)


class EulerpoolProvider:
    name = "eulerpool"
    capabilities = ProviderCapabilities(
        supported=frozenset(
            {
                MarketDataCapability.SYMBOL_SEARCH,
                MarketDataCapability.PROFILE,
                MarketDataCapability.IDENTIFIERS,
                MarketDataCapability.EXCHANGE,
                MarketDataCapability.LIVE_PRICE,
                MarketDataCapability.PRICE_HISTORY,
                MarketDataCapability.OHLCV,
            }
        ),
        instrument_types={
            MarketDataCapability.SYMBOL_SEARCH: _PROFILE_TYPES,
            MarketDataCapability.PROFILE: _PROFILE_TYPES,
            MarketDataCapability.IDENTIFIERS: _PROFILE_TYPES,
            MarketDataCapability.EXCHANGE: _PROFILE_TYPES,
            MarketDataCapability.LIVE_PRICE: _PROFILE_TYPES,
            MarketDataCapability.PRICE_HISTORY: _PROFILE_TYPES,
            MarketDataCapability.OHLCV: frozenset(
                {InstrumentType.STOCK, InstrumentType.UNKNOWN}
            ),
        },
        intervals={
            MarketDataCapability.OHLCV: frozenset(
                {"1", "5", "15", "30", "60", "D", "W", "M", "1d", "1wk", "1mo"}
            )
        },
    )

    def __init__(
        self,
        api_key,
        *,
        base_url="https://api.eulerpool.com/api/1",
        timeout=30.0,
        retry_policy=None,
        client=None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retry_policy = retry_policy or RetryPolicy()
        self._client = client
        self._exchange_cache = None

    def search(self, query, *, limit=20):
        payload = self._get(f"/screener/search/{quote(query, safe='')}")
        if not isinstance(payload, list):
            return []
        return [
            InstrumentSearchResult(
                symbol=str(item.get("ticker") or ""),
                name=str(item.get("name") or item.get("ticker") or ""),
                instrument_type=_type(item.get("type")),
                currency=item.get("currency") or None,
                isin=item.get("isin") or None,
                wkn=item.get("wkn") or None,
                source=self.name,
            )
            for item in payload[:limit]
            if item.get("ticker")
        ]

    def get_profile(self, instrument):
        identifier = instrument.isin or instrument.symbol
        if not identifier:
            raise InstrumentNotFoundError("Eulerpool requires ISIN or symbol")

        if instrument.instrument_type in {InstrumentType.ETF, InstrumentType.ETP}:
            resources = ["etf"]
        elif instrument.instrument_type == InstrumentType.STOCK:
            resources = ["equity"]
        else:
            resources = ["equity", "etf"]

        for resource in resources:
            try:
                profile = self._get(f"/{resource}/profile/{quote(identifier, safe='')}")
            except InstrumentNotFoundError:
                continue

            symbol = (
                profile.get("symbol")
                or instrument.symbol
                or profile.get("ticker")
                or identifier
            )
            exchange_name = _primary_exchange_name(profile)
            if not exchange_name:
                exchange_name = self._fetch_primary_exchange(identifier)
            exchange = self._resolve_exchange(exchange_name)

            return InstrumentProfile(
                symbol=symbol,
                name=profile.get("name") or symbol,
                instrument_type=(
                    InstrumentType.ETF if resource == "etf" else InstrumentType.STOCK
                ),
                currency=profile.get("currency") or instrument.currency,
                country_code=profile.get("country") or profile.get("domicile"),
                isin=profile.get("isin") or instrument.isin,
                wkn=profile.get("wkn"),
                exchange_code=exchange.code if exchange else None,
                exchange_name=exchange_name or (exchange.name if exchange else None),
                sector=profile.get("sector") or profile.get("assetClass"),
                industry=profile.get("industry") or profile.get("investmentSegment"),
                website=profile.get("website"),
                description=profile.get("description"),
                last_price=_number(profile.get("latestQuotes")),
                source=self.name,
            )

        raise InstrumentNotFoundError(identifier)

    def get_quote(self, instrument):
        request = type("Request", (), {"instrument": instrument})()
        points = self.get_price_history(request)
        if not points:
            raise InstrumentNotFoundError(instrument.identifier)
        point = points[-1]
        return MarketQuote(
            timestamp=point.timestamp,
            price=point.price,
            currency=point.currency,
            source=self.name,
        )

    def get_price_history(self, request):
        instrument = request.instrument
        identifier = instrument.isin or instrument.symbol
        if not identifier:
            raise InstrumentNotFoundError("Eulerpool requires ISIN or symbol")

        resources = (
            ["etf", "equity"]
            if instrument.instrument_type
            in {InstrumentType.ETF, InstrumentType.ETP, InstrumentType.UNKNOWN}
            else ["equity"]
        )

        for resource in resources:
            try:
                payload = self._get(f"/{resource}/quotes/{quote(identifier, safe='')}")
            except InstrumentNotFoundError:
                continue

            rows = []
            if isinstance(payload, list):
                for item in payload:
                    if (
                        isinstance(item, list)
                        and len(item) >= 2
                        and item[0] is not None
                        and item[1] is not None
                    ):
                        timestamp = int(item[0])
                        if timestamp > 10_000_000_000:
                            timestamp //= 1000
                        rows.append(
                            PricePoint(
                                timestamp,
                                float(item[1]),
                                instrument.currency,
                            )
                        )
            if rows:
                return rows

        raise InstrumentNotFoundError(identifier)

    def get_ohlcv(self, request):
        identifier = request.instrument.isin or request.instrument.symbol
        if not identifier:
            raise InstrumentNotFoundError("Eulerpool requires ISIN or symbol")

        resolution = {"1d": "D", "1wk": "W", "1mo": "M"}.get(
            request.interval,
            request.interval,
        )
        params = {"resolution": resolution}
        if request.from_timestamp is not None:
            params["from"] = request.from_timestamp
        if request.to_timestamp is not None:
            params["to"] = request.to_timestamp

        payload = self._get(
            f"/charting/ohlcv/{quote(identifier, safe='')}",
            params=params,
        )
        arrays = {key: payload.get(key) or [] for key in "tohlcv"}
        length = min(map(len, arrays.values())) if arrays else 0
        return [
            PriceBar(
                timestamp=int(arrays["t"][index]),
                open=float(arrays["o"][index]),
                high=float(arrays["h"][index]),
                low=float(arrays["l"][index]),
                close=float(arrays["c"][index]),
                volume=(
                    float(arrays["v"][index])
                    if arrays["v"][index] is not None
                    else None
                ),
            )
            for index in range(length)
        ]

    def _fetch_primary_exchange(self, identifier):
        try:
            payload = self._get(
                f"/market/quotes/exchanges/{quote(identifier, safe='')}"
            )
        except InstrumentNotFoundError:
            return None

        if isinstance(payload, dict):
            primary = payload.get("primary")
            if primary:
                return str(primary)
        return None

    def _resolve_exchange(self, name):
        if not name:
            return None
        if self._exchange_cache is None:
            payload = self._get("/market/exchanges")
            self._exchange_cache = [
                ExchangeReference(
                    code=item["code"],
                    name=item.get("name"),
                    country_code=item.get("country"),
                    timezone=item.get("timezone"),
                    source=self.name,
                )
                for item in payload
                if item.get("code")
            ]

        needle = name.strip().lower()
        aliases = {
            "milan": "xmil",
            "nasdaq": "xnas",
            "new york stock exchange": "xnys",
            "frankfurt": "xfra",
            "stuttgart": "xstu",
            "xetra": "xetr",
        }
        code = aliases.get(needle)

        for exchange in self._exchange_cache:
            if (
                exchange.code.lower() == needle
                or (code and exchange.code.lower() == code)
                or needle in (exchange.name or "").lower()
            ):
                return exchange
        return None

    def _get(self, path, params=None):
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=self.timeout)
        query = {"token": self.api_key, **(params or {})}

        try:
            try:
                response = request_with_retry(
                    client,
                    "GET",
                    self.base_url + path,
                    policy=self.retry_policy,
                    params=query,
                )
            except httpx.HTTPStatusError as error:
                if error.response.status_code == 404:
                    raise InstrumentNotFoundError(path) from error
                raise
            return response.json()
        finally:
            if owns_client:
                client.close()


def _type(value):
    return {
        "STOCK": InstrumentType.STOCK,
        "ETF": InstrumentType.ETF,
        "ETP": InstrumentType.ETP,
    }.get(str(value or "").upper(), InstrumentType.UNKNOWN)


def _number(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _primary_exchange_name(profile):
    for row in profile.get("tickers") or []:
        if (
            isinstance(row, dict)
            and row.get("exchange")
            and str(row["exchange"]).lower() not in {"mil"}
        ):
            return str(row["exchange"])
    return None
