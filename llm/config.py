from dataclasses import dataclass
from typing import Protocol


class SettingsReader(Protocol):
    def get(self, key: str, default=None): ...


def _get_bool(
    settings: SettingsReader,
    key: str,
    default: bool,
) -> bool:
    value = settings.get(key, default)

    if isinstance(value, bool):
        return value

    normalized = str(value).strip().lower()

    if normalized in {"1", "true", "yes", "on"}:
        return True

    if normalized in {"0", "false", "no", "off", ""}:
        return False

    raise ValueError(f"{key} must be a boolean value")


SUPPORTED_PROVIDERS = {"gemini", "ollama", "openai_compatible"}


@dataclass(frozen=True, slots=True)
class LLMConfig:
    provider: str
    model: str
    base_url: str = ""
    api_key: str = ""
    timeout: float = 60.0
    max_retries: int = 4
    tokens_per_minute: int = 0
    requests_per_minute: int = 0
    requests_per_day: int = 0
    daily_reset_timezone: str = "America/Los_Angeles"
    rate_limit_safety_factor: float = 0.90
    retry_max_delay: float = 120.0

    persistent_queue_enabled: bool = False
    queue_poll_seconds: float = 2.0
    queue_stale_seconds: int = 900
    queue_wait_timeout_seconds: float = 900.0

    queue_lease_seconds: int = 300
    queue_heartbeat_seconds: int = 60
    queue_maintenance_interval_seconds: int = 600
    queue_completed_retention_days: int = 7
    queue_failed_retention_days: int = 30
    queue_abandoned_retention_days: int = 30

    @classmethod
    def from_settings(
        cls,
        settings: SettingsReader,
        *,
        prefix: str,
        default_provider: str,
        default_timeout: float,
        default_max_retries: int = 4,
    ) -> "LLMConfig":
        normalized_prefix = prefix.strip().upper().rstrip("_")
        config = cls(
            provider=str(
                settings.get(f"{normalized_prefix}_PROVIDER", default_provider)
            )
            .strip()
            .lower(),
            base_url=str(settings.get(f"{normalized_prefix}_BASE_URL", "") or "")
            .strip()
            .rstrip("/"),
            api_key=str(settings.get(f"{normalized_prefix}_API_KEY", "") or "").strip(),
            model=str(settings.get(f"{normalized_prefix}_MODEL", "") or "").strip(),
            timeout=float(
                settings.get(f"{normalized_prefix}_TIMEOUT", default_timeout)
            ),
            max_retries=int(
                settings.get(f"{normalized_prefix}_MAX_RETRIES", default_max_retries)
            ),
            tokens_per_minute=int(
                settings.get(
                    f"{normalized_prefix}_TOKENS_PER_MINUTE",
                    0,
                )
                or 0
            ),
            requests_per_minute=int(
                settings.get(
                    f"{normalized_prefix}_REQUESTS_PER_MINUTE",
                    0,
                )
                or 0
            ),
            requests_per_day=int(
                settings.get(
                    f"{normalized_prefix}_REQUESTS_PER_DAY",
                    0,
                )
                or 0
            ),
            daily_reset_timezone=str(
                settings.get(
                    f"{normalized_prefix}_DAILY_RESET_TIMEZONE",
                    "America/Los_Angeles",
                )
                or "America/Los_Angeles"
            ).strip(),
            rate_limit_safety_factor=float(
                settings.get(
                    f"{normalized_prefix}_RATE_LIMIT_SAFETY_FACTOR",
                    0.90,
                )
            ),
            retry_max_delay=float(
                settings.get(
                    f"{normalized_prefix}_RETRY_MAX_DELAY",
                    120.0,
                )
            ),
            persistent_queue_enabled=_get_bool(
                settings,
                f"{normalized_prefix}_PERSISTENT_QUEUE_ENABLED",
                False,
            ),
            queue_poll_seconds=float(
                settings.get(
                    f"{normalized_prefix}_QUEUE_POLL_SECONDS",
                    2.0,
                )
            ),
            queue_stale_seconds=int(
                settings.get(
                    f"{normalized_prefix}_QUEUE_STALE_SECONDS",
                    900,
                )
            ),
            queue_wait_timeout_seconds=float(
                settings.get(
                    f"{normalized_prefix}_QUEUE_WAIT_TIMEOUT_SECONDS",
                    900.0,
                )
            ),
            queue_lease_seconds=int(
                settings.get(
                    f"{normalized_prefix}_QUEUE_LEASE_SECONDS",
                    300,
                )
            ),
            queue_heartbeat_seconds=int(
                settings.get(
                    f"{normalized_prefix}_QUEUE_HEARTBEAT_SECONDS",
                    60,
                )
            ),
            queue_maintenance_interval_seconds=int(
                settings.get(
                    (f"{normalized_prefix}_QUEUE_" "MAINTENANCE_INTERVAL_SECONDS"),
                    600,
                )
            ),
            queue_completed_retention_days=int(
                settings.get(
                    (f"{normalized_prefix}_QUEUE_" "COMPLETED_RETENTION_DAYS"),
                    7,
                )
            ),
            queue_failed_retention_days=int(
                settings.get(
                    (f"{normalized_prefix}_QUEUE_" "FAILED_RETENTION_DAYS"),
                    30,
                )
            ),
            queue_abandoned_retention_days=int(
                settings.get(
                    (f"{normalized_prefix}_QUEUE_" "ABANDONED_RETENTION_DAYS"),
                    30,
                )
            ),
        )
        config.validate(prefix=normalized_prefix)
        return config

    def validate(self, *, prefix: str = "LLM") -> None:
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ValueError(f"Unsupported {prefix}_PROVIDER: {self.provider}")
        if not self.model:
            raise ValueError(f"Missing {prefix}_MODEL")
        if self.provider in {"ollama", "openai_compatible"} and not self.base_url:
            raise ValueError(f"Missing {prefix}_BASE_URL")
        if self.provider in {"gemini", "openai_compatible"} and not self.api_key:
            raise ValueError(f"Missing {prefix}_API_KEY")
        if self.timeout <= 0:
            raise ValueError(f"{prefix}_TIMEOUT must be > 0")
        if self.max_retries < 0:
            raise ValueError(f"{prefix}_MAX_RETRIES must be >= 0")
        if (
            self.tokens_per_minute < 0
            or self.requests_per_minute < 0
            or self.requests_per_day < 0
        ):
            raise ValueError(f"{prefix} rate limits must be >= 0")
        if not self.daily_reset_timezone:
            raise ValueError(f"{prefix}_DAILY_RESET_TIMEZONE cannot be empty")
        if not 0 < self.rate_limit_safety_factor <= 1:
            raise ValueError(f"{prefix}_RATE_LIMIT_SAFETY_FACTOR must be in (0, 1]")
        if self.retry_max_delay < 0:
            raise ValueError(f"{prefix}_RETRY_MAX_DELAY must be >= 0")
        if self.queue_poll_seconds <= 0 or self.queue_stale_seconds <= 0:
            raise ValueError(f"{prefix} queue timing values must be > 0")
        if self.queue_wait_timeout_seconds <= 0:
            raise ValueError(f"{prefix}_QUEUE_WAIT_TIMEOUT_SECONDS must be > 0")
        if (
            self.tokens_per_minute < 0
            or self.requests_per_minute < 0
            or self.requests_per_day < 0
        ):
            raise ValueError(f"{prefix} rate limits must be >= 0")

        if not 0 < self.rate_limit_safety_factor <= 1:
            raise ValueError(f"{prefix}_RATE_LIMIT_SAFETY_FACTOR " "must be in (0, 1]")

        if self.retry_max_delay <= 0:
            raise ValueError(f"{prefix}_RETRY_MAX_DELAY must be > 0")

        if not self.daily_reset_timezone:
            raise ValueError(f"{prefix}_DAILY_RESET_TIMEZONE cannot be empty")

        if self.queue_poll_seconds <= 0:
            raise ValueError(f"{prefix}_QUEUE_POLL_SECONDS must be > 0")

        if self.queue_stale_seconds <= 0:
            raise ValueError(f"{prefix}_QUEUE_STALE_SECONDS must be > 0")

        if self.queue_wait_timeout_seconds <= 0:
            raise ValueError(f"{prefix}_QUEUE_WAIT_TIMEOUT_SECONDS must be > 0")

        if self.queue_lease_seconds <= 0:
            raise ValueError(f"{prefix}_QUEUE_LEASE_SECONDS must be > 0")

        if self.queue_heartbeat_seconds <= 0:
            raise ValueError(f"{prefix}_QUEUE_HEARTBEAT_SECONDS must be > 0")

        if self.queue_heartbeat_seconds >= self.queue_lease_seconds:
            raise ValueError(
                f"{prefix}_QUEUE_HEARTBEAT_SECONDS "
                "must be lower than "
                f"{prefix}_QUEUE_LEASE_SECONDS"
            )

        if self.queue_maintenance_interval_seconds <= 0:
            raise ValueError(
                f"{prefix}_QUEUE_MAINTENANCE_INTERVAL_SECONDS " "must be > 0"
            )

        if self.queue_completed_retention_days < 1:
            raise ValueError(f"{prefix}_QUEUE_COMPLETED_RETENTION_DAYS " "must be >= 1")

        if self.queue_failed_retention_days < 1:
            raise ValueError(f"{prefix}_QUEUE_FAILED_RETENTION_DAYS " "must be >= 1")

        if self.queue_abandoned_retention_days < 1:
            raise ValueError(f"{prefix}_QUEUE_ABANDONED_RETENTION_DAYS " "must be >= 1")
