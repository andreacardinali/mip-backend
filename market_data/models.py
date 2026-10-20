from dataclasses import asdict, dataclass, field, replace
from datetime import date
from decimal import Decimal
from typing import Any

from market_data.capabilities import InstrumentType


@dataclass(frozen=True, slots=True)
class InstrumentReference:
    symbol: str | None = None
    isin: str | None = None
    figi: str | None = None
    instrument_type: InstrumentType = InstrumentType.UNKNOWN
    currency: str | None = None
    exchange_code: str | None = None

    @property
    def identifier(self) -> str:
        value = self.isin or self.symbol or self.figi
        if not value:
            raise ValueError("Instrument reference requires symbol, ISIN, or FIGI")
        return value


@dataclass(frozen=True, slots=True)
class InstrumentSearchResult:
    symbol: str
    name: str
    instrument_type: InstrumentType = InstrumentType.UNKNOWN
    currency: str | None = None
    isin: str | None = None
    wkn: str | None = None
    exchange_code: str | None = None
    exchange_name: str | None = None
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["instrument_type"] = self.instrument_type.value
        payload["data_source"] = self.source
        payload["exchange"] = self.exchange_code
        payload["exchangeFullName"] = self.exchange_name
        return payload


@dataclass(frozen=True, slots=True)
class InstrumentProfile:
    symbol: str
    name: str
    instrument_type: InstrumentType = InstrumentType.UNKNOWN
    currency: str | None = None
    country_code: str | None = None
    isin: str | None = None
    wkn: str | None = None
    figi: str | None = None
    figi_composite: str | None = None
    figi_share_class: str | None = None
    exchange_code: str | None = None
    exchange_name: str | None = None
    sector: str | None = None
    industry: str | None = None
    website: str | None = None
    description: str | None = None
    last_price: float | None = None
    price_timestamp: int | None = None
    market_sector: str | None = None
    source: str | None = None
    provenance: dict[str, str] = field(default_factory=dict)

    def with_fields(self, **values):
        return replace(self, **values)

    def to_import_payload(self) -> dict[str, Any]:
        profile_source = (self.source or "").upper() or None
        price_source = (self.provenance.get("last_price") or "").upper() or None
        identifiers = {
            key: value
            for key, value in (
                ("ISIN", self.isin),
                ("WKN", self.wkn),
                ("FIGI", self.figi),
                ("FIGI_COMPOSITE", self.figi_composite),
                ("FIGI_SHARE_CLASS", self.figi_share_class),
            )
            if value
        }
        return {
            "symbol": self.symbol,
            "name": self.name,
            "instrument_type": self.instrument_type.value,
            "currency": self.currency,
            "country_code": self.country_code,
            "identifiers": identifiers,
            "isin": self.isin,
            "wkn": self.wkn,
            "figi": self.figi,
            "figi_composite": self.figi_composite,
            "figi_share_class": self.figi_share_class,
            "exchange": self.exchange_code,
            "exchangeFullName": self.exchange_name,
            "sector": self.sector,
            "industry": self.industry,
            "website": self.website,
            "description": self.description,
            "last_price": self.last_price,
            "price_timestamp": self.price_timestamp,
            "price_source": price_source,
            "market_sector": self.market_sector,
            "source": profile_source,
            "data_source": profile_source,
        }


@dataclass(frozen=True, slots=True)
class MarketQuote:
    timestamp: int
    price: float
    currency: str | None = None
    bid: float | None = None
    ask: float | None = None
    source: str | None = None


@dataclass(frozen=True, slots=True)
class PricePoint:
    timestamp: int
    price: float
    currency: str | None = None


@dataclass(frozen=True, slots=True)
class PriceBar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class HistoryRequest:
    instrument: InstrumentReference
    period: str = "1y"
    interval: str = "1d"
    from_timestamp: int | None = None
    to_timestamp: int | None = None


@dataclass(frozen=True, slots=True)
class ExchangeReference:
    code: str
    name: str | None = None
    country_code: str | None = None
    timezone: str | None = None
    source: str | None = None
    provider_code: str | None = None


@dataclass(frozen=True, slots=True)
class ExchangeRateSnapshot:
    source: str
    reference_date: date
    base_currency: str
    rates_to_usd: dict[str, Decimal]
