from dataclasses import dataclass, field
from enum import StrEnum


class MarketDataCapability(StrEnum):
    SYMBOL_SEARCH = "symbol_search"
    PROFILE = "profile"
    IDENTIFIERS = "identifiers"
    EXCHANGE = "exchange"
    LIVE_PRICE = "live_price"
    PRICE_HISTORY = "price_history"
    OHLCV = "ohlcv"
    EXCHANGE_RATES = "exchange_rates"
    NEWS = "news"
    ETF_HOLDINGS = "etf_holdings"
    EARNINGS_CALENDAR = "earnings_calendar"
    ECONOMIC_CALENDAR = "economic_calendar"


class InstrumentType(StrEnum):
    STOCK = "STOCK"
    ETF = "ETF"
    ETP = "ETP"
    FUND = "FUND"
    INDEX = "INDEX"
    CRYPTO = "CRYPTO"
    COMMODITY = "COMMODITY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    supported: frozenset[MarketDataCapability]
    instrument_types: dict[MarketDataCapability, frozenset[InstrumentType]] = field(
        default_factory=dict
    )
    intervals: dict[MarketDataCapability, frozenset[str]] = field(default_factory=dict)

    def supports(self, capability, *, instrument_type=None, interval=None):
        if capability not in self.supported:
            return False
        types = self.instrument_types.get(capability)
        intervals = self.intervals.get(capability)
        if instrument_type and types and instrument_type not in types:
            return False
        if interval and intervals and interval not in intervals:
            return False
        return True

    def to_dict(self):
        return {
            c.value: {
                "instrument_types": sorted(
                    x.value for x in self.instrument_types.get(c, frozenset())
                ),
                "intervals": sorted(self.intervals.get(c, frozenset())),
            }
            for c in sorted(self.supported, key=str)
        }
