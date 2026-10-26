from dataclasses import dataclass
from typing import Protocol

from market_sources.capabilities import MarketSourceCapability


class SettingsReader(Protocol):
    def get(self, key: str, default=None): ...


DEFAULT_ORDERS = {
    MarketSourceCapability.UNIVERSE_MEMBERS: ("wikipedia",),
    MarketSourceCapability.DISCOVERY: ("apewisdom",),
}


def _csv(value) -> tuple[str, ...]:
    return tuple(
        item.strip().lower() for item in str(value or "").split(",") if item.strip()
    )


@dataclass(frozen=True, slots=True)
class MarketSourceConfig:
    provider_orders: dict[MarketSourceCapability, tuple[str, ...]]
    http_timeout: float
    http_max_retries: int
    http_retry_base_seconds: float

    @classmethod
    def from_settings(cls, settings: SettingsReader) -> "MarketSourceConfig":
        orders = {}
        for capability, default in DEFAULT_ORDERS.items():
            key = f"MARKET_SOURCES_{capability.value.upper()}_ORDER"
            orders[capability] = _csv(settings.get(key, ",".join(default)))
        config = cls(
            provider_orders=orders,
            http_timeout=float(settings.get("MARKET_SOURCES_HTTP_TIMEOUT", 30.0)),
            http_max_retries=int(settings.get("MARKET_SOURCES_HTTP_MAX_RETRIES", 2)),
            http_retry_base_seconds=float(
                settings.get("MARKET_SOURCES_HTTP_RETRY_BASE_SECONDS", 1.0)
            ),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.http_timeout <= 0:
            raise ValueError("MARKET_SOURCES_HTTP_TIMEOUT must be > 0")
        if self.http_max_retries < 0:
            raise ValueError("MARKET_SOURCES_HTTP_MAX_RETRIES must be >= 0")
        if self.http_retry_base_seconds < 0:
            raise ValueError("MARKET_SOURCES_HTTP_RETRY_BASE_SECONDS must be >= 0")
        for capability, providers in self.provider_orders.items():
            if not providers:
                raise ValueError(f"No provider configured for {capability.value}")
