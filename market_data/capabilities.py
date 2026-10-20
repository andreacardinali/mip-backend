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
        allowed_types = self.instrument_types.get(capability)
        if instrument_type and allowed_types and instrument_type not in allowed_types:
            return False
        allowed_intervals = self.intervals.get(capability)
        if interval and allowed_intervals and interval not in allowed_intervals:
            return False
        return True

    def to_dict(self) -> dict:
        result = {}
        for capability in sorted(self.supported, key=str):
            result[capability.value] = {
                "instrument_types": sorted(
                    item.value
                    for item in self.instrument_types.get(capability, frozenset())
                ),
                "intervals": sorted(self.intervals.get(capability, frozenset())),
            }
        return result
