from dataclasses import dataclass
from typing import Protocol


class SettingsReader(Protocol):
    def get(self, key: str, default=None): ...


SUPPORTED_MARKET_DATA_PROVIDERS = {"yahoo"}


@dataclass(frozen=True, slots=True)
class MarketDataConfig:
    provider: str
    http_timeout: float
    http_max_retries: int
    http_retry_base_seconds: float

    @classmethod
    def from_settings(cls, settings: SettingsReader) -> "MarketDataConfig":
        config = cls(
            provider=str(settings.get("MARKET_DATA_PROVIDER", "yahoo")).strip().lower(),
            http_timeout=float(settings.get("MARKET_DATA_HTTP_TIMEOUT", 30.0)),
            http_max_retries=int(settings.get("MARKET_DATA_HTTP_MAX_RETRIES", 3)),
            http_retry_base_seconds=float(settings.get("MARKET_DATA_HTTP_RETRY_BASE_SECONDS", 1.0)),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.provider not in SUPPORTED_MARKET_DATA_PROVIDERS:
            raise ValueError(f"Unsupported MARKET_DATA_PROVIDER: {self.provider}")
        if self.http_timeout <= 0:
            raise ValueError("MARKET_DATA_HTTP_TIMEOUT must be > 0")
        if self.http_max_retries < 0:
            raise ValueError("MARKET_DATA_HTTP_MAX_RETRIES must be >= 0")
        if self.http_retry_base_seconds < 0:
            raise ValueError("MARKET_DATA_HTTP_RETRY_BASE_SECONDS must be >= 0")
