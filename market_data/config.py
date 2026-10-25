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
    MarketDataCapability.INDEX_CONSTITUENTS: ("wikipedia",),
    MarketDataCapability.EARNINGS_CALENDAR: ("eulerpool",),
    MarketDataCapability.ECONOMIC_CALENDAR: ("eulerpool",),
}


def _csv(value) -> tuple[str, ...]:
    return tuple(
        item.strip().lower() for item in str(value or "").split(",") if item.strip()
    )


@dataclass(frozen=True, slots=True)
class MarketDataConfig:
    provider_orders: dict[MarketDataCapability, tuple[str, ...]]
    http_timeout: float
    http_max_retries: int
    http_retry_base_seconds: float
    max_quote_age_hours: float = 96.0

    @classmethod
    def from_settings(cls, settings: SettingsReader) -> "MarketDataConfig":
        orders = {}
        for capability, default in DEFAULT_ORDERS.items():
            key = f"MARKET_DATA_{capability.value.upper()}_ORDER"
            orders[capability] = _csv(settings.get(key, ",".join(default)))
        config = cls(
            provider_orders=orders,
            http_timeout=float(settings.get("MARKET_DATA_HTTP_TIMEOUT", 30.0)),
            http_max_retries=int(settings.get("MARKET_DATA_HTTP_MAX_RETRIES", 2)),
            http_retry_base_seconds=float(
                settings.get("MARKET_DATA_HTTP_RETRY_BASE_SECONDS", 1.0)
            ),
            max_quote_age_hours=float(
                settings.get("MARKET_DATA_MAX_QUOTE_AGE_HOURS", 96.0)
            ),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.http_timeout <= 0:
            raise ValueError("MARKET_DATA_HTTP_TIMEOUT must be > 0")
        if self.http_max_retries < 0:
            raise ValueError("MARKET_DATA_HTTP_MAX_RETRIES must be >= 0")
        if self.max_quote_age_hours <= 0:
            raise ValueError("MARKET_DATA_MAX_QUOTE_AGE_HOURS must be > 0")
        for capability, providers in self.provider_orders.items():
            if not providers:
                raise ValueError(f"No provider configured for {capability.value}")
