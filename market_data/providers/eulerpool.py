from urllib.parse import quote

import httpx

from http_client.retry import RetryPolicy, request_with_retry
from market_data.capabilities import (
    InstrumentType,
    MarketDataCapability,
    ProviderCapabilities,
)
from market_data.exceptions import CapabilityUnavailableError, InstrumentNotFoundError
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
        payload = self._get(f"/screener/search/{quote(query,safe='')}")
        if not isinstance(payload, list):
            return []
        return [
            InstrumentSearchResult(
                symbol=str(x.get("ticker") or ""),
                name=str(x.get("name") or x.get("ticker") or ""),
                instrument_type=_type(x.get("type")),
                currency=x.get("currency") or None,
                isin=x.get("isin") or None,
                wkn=x.get("wkn") or None,
                source=self.name,
            )
            for x in payload[:limit]
            if x.get("ticker")
        ]

    def get_profile(self, instrument):
        identifier = instrument.isin or instrument.symbol
        if not identifier:
            raise InstrumentNotFoundError("Eulerpool requires ISIN or symbol")
        kind = instrument.instrument_type
        paths = []
        if kind in {InstrumentType.ETF, InstrumentType.ETP}:
            paths = ["etf"]
        elif kind == InstrumentType.STOCK:
            paths = ["equity"]
        else:
            paths = ["equity", "etf"]
        for resource in paths:
            try:
                p = self._get(f"/{resource}/profile/{quote(identifier,safe='')}")
            except InstrumentNotFoundError:
                continue
            symbol = (
                p.get("symbol") or instrument.symbol or p.get("ticker") or identifier
            )
            exchange_name = _primary_exchange_name(p)
            exchange = self._resolve_exchange(exchange_name)
            return InstrumentProfile(
                symbol=symbol,
                name=p.get("name") or symbol,
                instrument_type=(
                    InstrumentType.ETF if resource == "etf" else InstrumentType.STOCK
                ),
                currency=p.get("currency") or instrument.currency,
                country_code=p.get("country") or p.get("domicile"),
                isin=p.get("isin") or instrument.isin,
                wkn=p.get("wkn"),
                exchange_code=exchange.code if exchange else None,
                exchange_name=exchange_name or (exchange.name if exchange else None),
                sector=p.get("sector") or p.get("assetClass"),
                industry=p.get("industry") or p.get("investmentSegment"),
                website=p.get("website"),
                description=p.get("description"),
                last_price=_number(p.get("latestQuotes")),
                source=self.name,
            )
        raise InstrumentNotFoundError(identifier)

    def get_quote(self, instrument):
        points = self.get_price_history(
            type("Request", (), {"instrument": instrument})()
        )
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
        inst = request.instrument
        identifier = inst.isin or inst.symbol
        if not identifier:
            raise InstrumentNotFoundError("Eulerpool requires ISIN or symbol")
        resources = (
            ["etf", "equity"]
            if inst.instrument_type
            in {InstrumentType.ETF, InstrumentType.ETP, InstrumentType.UNKNOWN}
            else ["equity"]
        )
        for resource in resources:
            try:
                payload = self._get(f"/{resource}/quotes/{quote(identifier,safe='')}")
            except InstrumentNotFoundError:
                continue
            if isinstance(payload, list):
                rows = []
                for item in payload:
                    if (
                        isinstance(item, list)
                        and len(item) >= 2
                        and item[0] is not None
                        and item[1] is not None
                    ):
                        timestamp = int(item[0])
                        timestamp = (
                            timestamp // 1000
                            if timestamp > 10_000_000_000
                            else timestamp
                        )
                        rows.append(
                            PricePoint(timestamp, float(item[1]), inst.currency)
                        )
                if rows:
                    return rows
        raise InstrumentNotFoundError(identifier)

    def get_ohlcv(self, request):
        if request.instrument.instrument_type in {
            InstrumentType.ETF,
            InstrumentType.ETP,
        }:
            raise CapabilityUnavailableError(
                "Eulerpool OHLCV is not declared for ETF/ETP"
            )
        identifier = request.instrument.isin or request.instrument.symbol
        resolution = {"1d": "D", "1wk": "W", "1mo": "M"}.get(
            request.interval, request.interval
        )
        params = {"resolution": resolution}
        if request.from_timestamp is not None:
            params["from"] = request.from_timestamp
        if request.to_timestamp is not None:
            params["to"] = request.to_timestamp
        payload = self._get(
            f"/charting/ohlcv/{quote(identifier,safe='')}", params=params
        )
        arrays = {k: payload.get(k) or [] for k in "tohlcv"}
        length = min(map(len, arrays.values())) if arrays else 0
        return [
            PriceBar(
                int(arrays["t"][i]),
                float(arrays["o"][i]),
                float(arrays["h"][i]),
                float(arrays["l"][i]),
                float(arrays["c"][i]),
                float(arrays["v"][i]) if arrays["v"][i] is not None else None,
            )
            for i in range(length)
        ]

    def _resolve_exchange(self, name):
        if not name:
            return None
        if self._exchange_cache is None:
            payload = self._get("/market/exchanges")
            self._exchange_cache = [
                ExchangeReference(
                    code=x["code"],
                    name=x.get("name"),
                    country_code=x.get("country"),
                    timezone=x.get("timezone"),
                    source=self.name,
                )
                for x in payload
                if x.get("code")
            ]
        needle = name.lower()
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
                or exchange.code.lower() == str(code).lower()
                or needle in (exchange.name or "").lower()
            ):
                return exchange
        return None

    def _get(self, path, params=None):
        owns = self._client is None
        client = self._client or httpx.Client(timeout=self.timeout)
        query = {"token": self.api_key, **(params or {})}
        try:
            response = request_with_retry(
                client,
                "GET",
                self.base_url + path,
                policy=self.retry_policy,
                params=query,
            )
            if response.status_code == 404:
                raise InstrumentNotFoundError(path)
            return response.json()
        finally:
            if owns:
                client.close()


def _type(value):
    value = str(value or "").upper()
    return {
        "STOCK": InstrumentType.STOCK,
        "ETF": InstrumentType.ETF,
        "ETP": InstrumentType.ETP,
    }.get(value, InstrumentType.UNKNOWN)


def _number(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _primary_exchange_name(profile):
    tickers = profile.get("tickers") or []
    for row in tickers:
        if (
            isinstance(row, dict)
            and row.get("exchange")
            and str(row.get("exchange")).lower() not in {"mil"}
        ):
            return row["exchange"]
    return None
