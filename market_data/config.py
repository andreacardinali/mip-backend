from dataclasses import dataclass
from typing import Protocol

from market_data.capabilities import MarketDataCapability


class SettingsReader(Protocol):
    def get(self, key: str, default=None): ...


DEFAULT_ORDERS = {
    MarketDataCapability.SYMBOL_SEARCH: ("yahoo", "eulerpool", "ghostfolio", "fmp"),
    MarketDataCapability.PROFILE: ("eulerpool", "ghostfolio", "fmp"),
    MarketDataCapability.IDENTIFIERS: ("openfigi", "eulerpool", "ghostfolio"),
    MarketDataCapability.EXCHANGE: ("eulerpool", "openfigi", "fmp"),
    MarketDataCapability.LIVE_PRICE: ("eulerpool", "yahoo", "ghostfolio"),
    MarketDataCapability.PRICE_HISTORY: ("eulerpool", "yahoo"),
    MarketDataCapability.OHLCV: ("eulerpool", "yahoo"),
    MarketDataCapability.EXCHANGE_RATES: ("ecb",),
    MarketDataCapability.NEWS: ("eulerpool",),
    MarketDataCapability.ETF_HOLDINGS: ("eulerpool",),
    MarketDataCapability.EARNINGS_CALENDAR: ("eulerpool",),
    MarketDataCapability.ECONOMIC_CALENDAR: ("eulerpool",),
}


def _csv(value):
    return tuple(x.strip().lower() for x in str(value or "").split(",") if x.strip())


@dataclass(frozen=True, slots=True)
class MarketDataConfig:
    provider_orders: dict[MarketDataCapability, tuple[str, ...]]
    http_timeout: float
    http_max_retries: int
    http_retry_base_seconds: float
    max_quote_age_hours: float = 96.0

    @classmethod
    def from_settings(cls, settings):
        result = cls(
            {
                c: _csv(
                    settings.get(f"MARKET_DATA_{c.value.upper()}_ORDER", ",".join(d))
                )
                for c, d in DEFAULT_ORDERS.items()
            },
            float(settings.get("MARKET_DATA_HTTP_TIMEOUT", 30)),
            int(settings.get("MARKET_DATA_HTTP_MAX_RETRIES", 2)),
            float(settings.get("MARKET_DATA_HTTP_RETRY_BASE_SECONDS", 1)),
            float(settings.get("MARKET_DATA_MAX_QUOTE_AGE_HOURS", 96)),
        )
        result.validate()
        return result

    def validate(self):
        if self.http_timeout <= 0:
            raise ValueError("MARKET_DATA_HTTP_TIMEOUT must be > 0")
        if self.http_max_retries < 0:
            raise ValueError("MARKET_DATA_HTTP_MAX_RETRIES must be >= 0")
        if self.max_quote_age_hours <= 0:
            raise ValueError("MARKET_DATA_MAX_QUOTE_AGE_HOURS must be > 0")
        for c, p in self.provider_orders.items():
            if not p:
                raise ValueError(f"No provider configured for {c.value}")
