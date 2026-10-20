from datetime import UTC, date, datetime
from urllib.parse import quote

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
    NewsArticle,
)
from market_data.providers.eulerpool import EulerpoolProvider

_PROFILE_TYPES = frozenset(
    {
        InstrumentType.STOCK,
        InstrumentType.ETF,
        InstrumentType.ETP,
        InstrumentType.UNKNOWN,
    }
)


class EulerpoolIntelligenceProvider(EulerpoolProvider):
    """Eulerpool provider extended with normalized Market Intelligence capabilities."""

    capabilities = ProviderCapabilities(
        supported=EulerpoolProvider.capabilities.supported
        | frozenset(
            {
                MarketDataCapability.NEWS,
                MarketDataCapability.ETF_HOLDINGS,
                MarketDataCapability.EARNINGS_CALENDAR,
                MarketDataCapability.ECONOMIC_CALENDAR,
            }
        ),
        instrument_types={
            **EulerpoolProvider.capabilities.instrument_types,
            MarketDataCapability.NEWS: _PROFILE_TYPES,
            MarketDataCapability.ETF_HOLDINGS: frozenset(
                {InstrumentType.ETF, InstrumentType.ETP, InstrumentType.UNKNOWN}
            ),
        },
        intervals=dict(EulerpoolProvider.capabilities.intervals),
    )

    def get_news(self, instrument, *, limit=20):
        identifier = instrument.symbol or instrument.isin
        if not identifier:
            raise InstrumentNotFoundError("Eulerpool news requires symbol or ISIN")
        payload = self._get(
            f"/research/news/{quote(identifier, safe='')}", params={"limit": limit}
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
                    external_id=str(item["id"]) if item.get("id") is not None else None,
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
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
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
