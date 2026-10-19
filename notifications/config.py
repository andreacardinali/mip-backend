from dataclasses import dataclass

from config.settings import settings


def _as_bool(name: str, default: bool) -> bool:
    value = settings.get(name, str(default))
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class NotificationConfig:
    enabled: bool
    batch_size: int
    http_timeout_seconds: float
    max_retries: int
    retry_base_seconds: int
    processing_timeout_seconds: int

    @classmethod
    def load(cls) -> "NotificationConfig":
        config = cls(
            enabled=_as_bool("NOTIFICATIONS_ENABLED", True),
            batch_size=int(settings.get("NOTIFICATIONS_BATCH_SIZE", 50)),
            http_timeout_seconds=float(
                settings.get("NOTIFICATIONS_HTTP_TIMEOUT_SECONDS", 30.0)
            ),
            max_retries=int(settings.get("NOTIFICATIONS_MAX_RETRIES", 4)),
            retry_base_seconds=int(
                settings.get("NOTIFICATIONS_RETRY_BASE_SECONDS", 30)
            ),
            processing_timeout_seconds=int(
                settings.get("NOTIFICATIONS_PROCESSING_TIMEOUT_SECONDS", 300)
            ),
        )

        if config.batch_size < 1:
            raise ValueError("NOTIFICATIONS_BATCH_SIZE must be >= 1")
        if config.http_timeout_seconds <= 0:
            raise ValueError("NOTIFICATIONS_HTTP_TIMEOUT_SECONDS must be > 0")
        if config.max_retries < 0:
            raise ValueError("NOTIFICATIONS_MAX_RETRIES must be >= 0")
        if config.retry_base_seconds < 1:
            raise ValueError("NOTIFICATIONS_RETRY_BASE_SECONDS must be >= 1")
        if config.processing_timeout_seconds < 1:
            raise ValueError("NOTIFICATIONS_PROCESSING_TIMEOUT_SECONDS must be >= 1")

        return config
