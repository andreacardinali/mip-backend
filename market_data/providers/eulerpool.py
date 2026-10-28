from datetime import UTC, date, datetime
from urllib.parse import quote

from http_client import (
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
from market_data.exceptions import InstrumentNotFoundError
from market_data.models import (
    EarningsEvent,
    EconomicEvent,
    EtfExposure,
    EtfHolding,
    ExchangeReference,
    InstrumentProfile,
    InstrumentSearchResult,
    MarketQuote,
    NewsArticle,
    PriceBar,
    PricePoint,
)
from market_time.holidays import normalize_exchange_code, normalize_holidays

_PROFILE_TYPES = frozenset(
    {
        InstrumentType.STOCK,
        InstrumentType.ETF,
        InstrumentType.ETP,
        InstrumentType.UNKNOWN,
    }
)


class EulerpoolProvider:
    """Unified Eulerpool provider for market data and market intelligence."""

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
                MarketDataCapability.NEWS,
                MarketDataCapability.ETF_HOLDINGS,
                MarketDataCapability.EARNINGS_CALENDAR,
                MarketDataCapability.ECONOMIC_CALENDAR,
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
            MarketDataCapability.NEWS: _PROFILE_TYPES,
            MarketDataCapability.ETF_HOLDINGS: frozenset(
                {
                    InstrumentType.ETF,
                    InstrumentType.ETP,
                    InstrumentType.UNKNOWN,
                }
            ),
        },
        intervals={
            MarketDataCapability.OHLCV: frozenset(
                {
                    "1",
                    "5",
                    "15",
                    "30",
                    "60",
                    "D",
                    "W",
                    "M",
                    "1d",
                    "1wk",
                    "1mo",
                }
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
        self._exact_listing_cache = {}

    def search(self, query, *, limit=20):
        payload = self._get(f"/screener/search/{quote(query, safe='')}")
        if not isinstance(payload, list):
            return []
        results = [
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
        if _looks_like_isin(query):
            return [self._enrich_search_result(item) for item in results]
        return results

    def _enrich_search_result(self, result):
        try:
            profile = self.get_profile(
                type(
                    "Reference",
                    (),
                    {
                        "symbol": result.symbol,
                        "isin": result.isin,
                        "instrument_type": result.instrument_type,
                        "currency": result.currency or _listing_hints(result.symbol)[0],
                        "exchange_code": result.exchange_code
                        or _listing_hints(result.symbol)[1],
                    },
                )()
            )
        except InstrumentNotFoundError:
            return result
        return InstrumentSearchResult(
            symbol=result.symbol,
            name=result.name or profile.name,
            instrument_type=(
                result.instrument_type
                if result.instrument_type != InstrumentType.UNKNOWN
                else profile.instrument_type
            ),
            currency=profile.currency or result.currency,
            isin=result.isin or profile.isin,
            wkn=result.wkn or profile.wkn,
            exchange_code=profile.exchange_code,
            exchange_name=profile.exchange_name,
            source=self.name,
        )

    def get_profile(self, instrument):
        identifiers = [value for value in (instrument.symbol, instrument.isin) if value]
        if not identifiers:
            raise InstrumentNotFoundError("Eulerpool requires symbol or ISIN")
        resources = (
            ["etf"]
            if instrument.instrument_type in {InstrumentType.ETF, InstrumentType.ETP}
            else (
                ["equity"]
                if instrument.instrument_type == InstrumentType.STOCK
                else ["equity", "etf"]
            )
        )
        for identifier in identifiers:
            for resource in resources:
                try:
                    profile = self._get(
                        f"/{resource}/profile/{quote(identifier, safe='')}"
                    )
                except InstrumentNotFoundError:
                    continue
                symbol = (
                    instrument.symbol
                    or profile.get("symbol")
                    or profile.get("ticker")
                    or identifier
                )
                hinted_currency, hinted_exchange = _listing_hints(symbol)
                exchange_name = _primary_exchange_name(profile)
                if not exchange_name and not (
                    instrument.exchange_code or hinted_exchange
                ):
                    exchange_name = self._fetch_primary_exchange(identifier)
                exchange = self._resolve_exchange(exchange_name)
                return InstrumentProfile(
                    symbol=symbol,
                    name=profile.get("name") or symbol,
                    instrument_type=(
                        InstrumentType.ETF
                        if resource == "etf"
                        else InstrumentType.STOCK
                    ),
                    currency=(
                        instrument.currency
                        or hinted_currency
                        or profile.get("currency")
                    ),
                    country_code=profile.get("country") or profile.get("domicile"),
                    isin=instrument.isin or profile.get("isin"),
                    wkn=profile.get("wkn"),
                    exchange_code=(
                        instrument.exchange_code
                        or hinted_exchange
                        or (exchange.code if exchange else None)
                    ),
                    exchange_name=(
                        exchange_name
                        or (exchange.name if exchange else None)
                        or _exchange_name(hinted_exchange)
                    ),
                    sector=profile.get("sector") or profile.get("assetClass"),
                    industry=profile.get("industry")
                    or profile.get("investmentSegment"),
                    website=profile.get("website"),
                    description=profile.get("description"),
                    last_price=_number(profile.get("latestQuotes")),
                    source=self.name,
                )
        raise InstrumentNotFoundError(identifiers[0])

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
        if instrument.symbol:
            if not self._supports_exact_listing(instrument.symbol):
                raise InstrumentNotFoundError(
                    f"Eulerpool does not resolve exact listing {instrument.symbol}"
                )
            identifiers = [instrument.symbol]
        elif instrument.isin:
            identifiers = [instrument.isin]
        else:
            raise InstrumentNotFoundError("Eulerpool requires symbol or ISIN")
        resources = (
            ["etf", "equity"]
            if instrument.instrument_type
            in {InstrumentType.ETF, InstrumentType.ETP, InstrumentType.UNKNOWN}
            else ["equity"]
        )
        for identifier in identifiers:
            for resource in resources:
                try:
                    payload = self._get(
                        f"/{resource}/quotes/{quote(identifier, safe='')}"
                    )
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
        raise InstrumentNotFoundError(identifiers[0])

    def get_ohlcv(self, request):
        identifier = request.instrument.symbol or request.instrument.isin
        if not identifier:
            raise InstrumentNotFoundError("Eulerpool requires symbol or ISIN")
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

    def get_news(self, instrument, *, limit=20):
        identifier = instrument.symbol or instrument.isin
        if not identifier:
            raise InstrumentNotFoundError("Eulerpool news requires symbol or ISIN")
        payload = self._get(
            f"/research/news/{quote(identifier, safe='')}",
            params={"limit": limit},
        )
        if not isinstance(payload, list):
            return []
        result = []
        for item in payload[:limit]:
            if not isinstance(item, dict) or not item.get("headline"):
                continue
            published_at = _datetime_from_unix(item.get("datetime"))
            if published_at is None:
                continue
            result.append(
                NewsArticle(
                    headline=str(item["headline"]),
                    published_at=published_at,
                    url=item.get("realUrl") or item.get("url"),
                    summary=item.get("summary"),
                    source_name=item.get("source"),
                    category=item.get("category"),
                    related_symbols=tuple(
                        part.strip()
                        for part in str(item.get("related") or "").split(",")
                        if part.strip()
                    ),
                    external_id=(
                        str(item["id"]) if item.get("id") is not None else None
                    ),
                    image_url=item.get("image"),
                    source=self.name,
                )
            )
        return result

    def get_etf_holdings(self, instrument, *, limit=None):
        identifier = instrument.isin or instrument.symbol
        if not identifier:
            raise InstrumentNotFoundError(
                "Eulerpool ETF holdings require symbol or ISIN"
            )
        payload = self._get(f"/etf/holdings/{quote(identifier, safe='')}")
        if not isinstance(payload, list):
            return []
        rows = payload if limit is None else payload[:limit]
        result = []
        for item in rows:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            weight = _number(item.get("percent"))
            if weight is None:
                continue
            result.append(
                EtfHolding(
                    etf_symbol=str(item.get("etf_symbol") or identifier),
                    name=str(item["name"]),
                    weight_percent=weight,
                    symbol=item.get("symbol"),
                    isin=item.get("isin"),
                    cusip=item.get("cusip"),
                    asset_type=item.get("assetType"),
                    shares=_number(item.get("share")),
                    market_value=_number(item.get("value")),
                    updated_at=_datetime_from_iso(item.get("updated_at")),
                    source=self.name,
                )
            )
        return result

    def get_etf_countries(self, instrument):
        return self._get_etf_exposure(instrument, "countries", "country")

    def get_etf_sectors(self, instrument):
        try:
            return self._get_etf_exposure(instrument, "sectors", "sector")
        except InstrumentNotFoundError:
            return []

    def _get_etf_exposure(self, instrument, resource, name_key):
        identifier = instrument.isin or instrument.symbol
        if not identifier:
            raise InstrumentNotFoundError(
                "Eulerpool ETF exposure requires symbol or ISIN"
            )
        payload = self._get(f"/etf/{resource}/{quote(identifier, safe='')}")
        if not isinstance(payload, list):
            return []
        return [
            EtfExposure(
                name=str(item[name_key]),
                exposure=float(item["exposure"]),
                symbol=item.get("symbol"),
                source=self.name,
            )
            for item in payload
            if isinstance(item, dict)
            and item.get(name_key)
            and _number(item.get("exposure")) is not None
        ]

    def get_earnings_calendar(self, event_date, *, symbol=None):
        path = (
            f"/calendar/earnings-by-symbol/{quote(symbol, safe='')}"
            if symbol
            else f"/calendar/earnings/{quote(_date_value(event_date), safe='')}"
        )
        payload = self._get(path)
        if not isinstance(payload, list):
            return []
        rows = (_earnings_event(item, self.name) for item in payload)
        return sorted(
            (item for item in rows if item is not None),
            key=lambda item: item.event_date,
        )

    def get_economic_calendar(self, *, limit=None):
        payload = self._get(
            "/calendar/economic-calendar",
            params={"limit": limit} if limit is not None else None,
        )
        if not isinstance(payload, list):
            return []
        rows = payload if limit is None else payload[:limit]
        result = []
        for item in rows:
            if (
                not isinstance(item, dict)
                or item.get("release_id") is None
                or not item.get("release_name")
                or not item.get("release_date")
            ):
                continue
            try:
                release_date = date.fromisoformat(str(item["release_date"])[:10])
            except ValueError:
                continue
            result.append(
                EconomicEvent(
                    external_id=str(item["release_id"]),
                    name=str(item["release_name"]),
                    release_date=release_date,
                    notes=item.get("notes") or None,
                    source=self.name,
                )
            )
        return result

    def _supports_exact_listing(self, symbol):
        normalized = str(symbol or "").strip().upper()
        if not normalized:
            return False
        cached = self._exact_listing_cache.get(normalized)
        if cached is not None:
            return cached
        payload = self._get(f"/screener/search/{quote(normalized, safe='')}")
        supported = isinstance(payload, list) and any(
            str(item.get("ticker") or "").strip().upper() == normalized
            for item in payload
            if isinstance(item, dict)
        )
        self._exact_listing_cache[normalized] = supported
        return supported

    def _fetch_primary_exchange(self, identifier):
        try:
            payload = self._get(
                f"/market/quotes/exchanges/{quote(identifier, safe='')}"
            )
        except InstrumentNotFoundError:
            return None
        return (
            str(payload.get("primary"))
            if isinstance(payload, dict) and payload.get("primary")
            else None
        )

    def get_exchanges(self):
        if self._exchange_cache is None:
            payload = self._get("/market/exchanges")
            if not isinstance(payload, list):
                raise ValueError("Exchange catalog must be a list")
            self._exchange_cache = [
                ExchangeReference(
                    code=normalize_exchange_code(
                        item.get("exchange_code") or item["code"]
                    ),
                    name=item.get("name"),
                    country_code=item.get("country"),
                    timezone=item.get("timezone"),
                    source=self.name,
                )
                for item in payload
                if isinstance(item, dict)
                and (item.get("exchange_code") or item.get("code"))
            ]
        return tuple(self._exchange_cache)

    def get_exchange_holidays(self, exchange_code):
        code = normalize_exchange_code(exchange_code)
        payload = self._get(f"/market/holidays/{quote(code, safe='')}")
        return normalize_holidays(code, payload, source=self.name)

    def _resolve_exchange(self, name):
        if not name:
            return None
        needle = name.strip().casefold()
        exchanges = self.get_exchanges()
        codes = [
            exchange for exchange in exchanges if exchange.code.casefold() == needle
        ]
        if len(codes) == 1:
            return codes[0]
        matches = [
            exchange
            for exchange in exchanges
            if (exchange.name or "").strip().casefold() == needle
        ]
        return matches[0] if not codes and len(matches) == 1 else None

    def _get(self, path, params=None):
        owns_client = self._client is None
        client = self._client or create_http_client(timeout=self.timeout)
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
            except HttpStatusError as error:
                if error.response.status_code == 404:
                    raise InstrumentNotFoundError(path) from error
                raise
            return response.json()
        finally:
            if owns_client:
                client.close()


def _listing_hints(symbol):
    value = str(symbol or "").strip().upper()
    suffix_map = {
        ".PA": ("EUR", "XPAR"),
        ".AS": ("EUR", "XAMS"),
        ".MI": ("EUR", "XMIL"),
        ".DE": ("EUR", "XETR"),
        ".F": ("EUR", "XFRA"),
        ".SW": ("CHF", "XSWX"),
        ".L": ("GBP", "XLON"),
    }
    currency = "EUR" if "-EUR." in value else None
    for suffix, (default_currency, exchange_code) in suffix_map.items():
        if value.endswith(suffix):
            return currency or default_currency, exchange_code
    return currency, None


def _exchange_name(code):
    return {
        "XPAR": "Euronext Paris",
        "XAMS": "Euronext Amsterdam",
        "XMIL": "Milan",
        "XETR": "XETRA",
        "XFRA": "Frankfurt",
        "XSWX": "SIX Swiss Exchange",
        "XLON": "London Stock Exchange",
    }.get(code)


def _looks_like_isin(value):
    compact = str(value or "").strip().upper()
    return (
        len(compact) == 12
        and compact[:2].isalpha()
        and compact[-1].isdigit()
        and compact[2:11].isalnum()
    )


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


def _integer(value):
    try:
        return int(value) if value is not None else None
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


def _date_value(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(str(value)[:10]).isoformat()


def _datetime_from_unix(value):
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    return datetime.fromtimestamp(timestamp, tz=UTC)


def _datetime_from_iso(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(UTC)
    except ValueError:
        return None


def _earnings_event(item, source):
    if not isinstance(item, dict) or not item.get("symbol") or not item.get("date"):
        return None
    try:
        event_date = date.fromisoformat(str(item["date"])[:10])
    except ValueError:
        return None
    return EarningsEvent(
        symbol=str(item["symbol"]),
        event_date=event_date,
        fiscal_year=_integer(item.get("year")),
        fiscal_quarter=_integer(item.get("quarter")),
        timing=item.get("hour") or None,
        eps_actual=_number(item.get("epsActual")),
        eps_estimate=_number(item.get("epsEstimate")),
        revenue_actual=_number(item.get("revenueActual")),
        revenue_estimate=_number(item.get("revenueEstimate")),
        isin=item.get("isin") or None,
        company_name=item.get("name") or item.get("route_name") or None,
        source=source,
    )
