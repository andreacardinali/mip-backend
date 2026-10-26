import json
from dataclasses import dataclass, field
from typing import Any, Protocol


class SettingsReader(Protocol):
    def get(self, key: str, default=None): ...


def _get_bool(settings: SettingsReader, key: str, default: bool) -> bool:
    value = settings.get(key, default)
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"{key} must be a boolean value")


def _get_json_object(settings: SettingsReader, key: str) -> dict[str, Any]:
    raw_value = settings.get(key, "")
    if raw_value is None:
        return {}
    if isinstance(raw_value, dict):
        return dict(raw_value)
    value = str(raw_value).strip()
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{key} must contain valid JSON: {exc.msg} at line "
            f"{exc.lineno}, column {exc.colno}"
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{key} must contain a JSON object")
    return parsed


SUPPORTED_PROVIDERS = {"gemini", "ollama", "openai_compatible"}
PROTECTED_JSON_CONFIG_KEYS = frozenset(
    {"model", "messages", "temperature", "max_tokens"}
)


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
    think: bool = False
    json_config: dict[str, Any] = field(default_factory=dict)
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
    cache_enabled: bool = True
    cache_ttl_seconds: int = 86400
    cache_grounded_ttl_seconds: int = 900
    cache_poll_seconds: float = 0.5
    cache_wait_timeout_seconds: float = 900.0
    cache_claim_stale_seconds: int = 900

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
        p = prefix.strip().upper().rstrip("_")
        config = cls(
            provider=str(settings.get(f"{p}_PROVIDER", default_provider))
            .strip()
            .lower(),
            model=str(settings.get(f"{p}_MODEL", "") or "").strip(),
            base_url=str(settings.get(f"{p}_BASE_URL", "") or "").strip().rstrip("/"),
            api_key=str(settings.get(f"{p}_API_KEY", "") or "").strip(),
            timeout=float(settings.get(f"{p}_TIMEOUT", default_timeout)),
            max_retries=int(settings.get(f"{p}_MAX_RETRIES", default_max_retries)),
            tokens_per_minute=int(settings.get(f"{p}_TOKENS_PER_MINUTE", 0) or 0),
            requests_per_minute=int(settings.get(f"{p}_REQUESTS_PER_MINUTE", 0) or 0),
            requests_per_day=int(settings.get(f"{p}_REQUESTS_PER_DAY", 0) or 0),
            daily_reset_timezone=str(
                settings.get(f"{p}_DAILY_RESET_TIMEZONE", "America/Los_Angeles")
                or "America/Los_Angeles"
            ).strip(),
            rate_limit_safety_factor=float(
                settings.get(f"{p}_RATE_LIMIT_SAFETY_FACTOR", 0.90)
            ),
            retry_max_delay=float(settings.get(f"{p}_RETRY_MAX_DELAY", 120.0)),
            think=_get_bool(settings, f"{p}_THINK", False),
            json_config=_get_json_object(settings, f"{p}_JSON_CONFIG"),
            persistent_queue_enabled=_get_bool(
                settings, f"{p}_PERSISTENT_QUEUE_ENABLED", False
            ),
            queue_poll_seconds=float(settings.get(f"{p}_QUEUE_POLL_SECONDS", 2.0)),
            queue_stale_seconds=int(settings.get(f"{p}_QUEUE_STALE_SECONDS", 900)),
            queue_wait_timeout_seconds=float(
                settings.get(f"{p}_QUEUE_WAIT_TIMEOUT_SECONDS", 900.0)
            ),
            queue_lease_seconds=int(settings.get(f"{p}_QUEUE_LEASE_SECONDS", 300)),
            queue_heartbeat_seconds=int(
                settings.get(f"{p}_QUEUE_HEARTBEAT_SECONDS", 60)
            ),
            queue_maintenance_interval_seconds=int(
                settings.get(f"{p}_QUEUE_MAINTENANCE_INTERVAL_SECONDS", 600)
            ),
            queue_completed_retention_days=int(
                settings.get(f"{p}_QUEUE_COMPLETED_RETENTION_DAYS", 7)
            ),
            queue_failed_retention_days=int(
                settings.get(f"{p}_QUEUE_FAILED_RETENTION_DAYS", 30)
            ),
            queue_abandoned_retention_days=int(
                settings.get(f"{p}_QUEUE_ABANDONED_RETENTION_DAYS", 30)
            ),
            cache_enabled=_get_bool(settings, f"{p}_CACHE_ENABLED", True),
            cache_ttl_seconds=int(settings.get(f"{p}_CACHE_TTL_SECONDS", 86400)),
            cache_grounded_ttl_seconds=int(
                settings.get(f"{p}_CACHE_GROUNDED_TTL_SECONDS", 900)
            ),
            cache_poll_seconds=float(settings.get(f"{p}_CACHE_POLL_SECONDS", 0.5)),
            cache_wait_timeout_seconds=float(
                settings.get(f"{p}_CACHE_WAIT_TIMEOUT_SECONDS", 900.0)
            ),
            cache_claim_stale_seconds=int(
                settings.get(f"{p}_CACHE_CLAIM_STALE_SECONDS", 900)
            ),
        )
        config.validate(prefix=p)
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
            min(self.tokens_per_minute, self.requests_per_minute, self.requests_per_day)
            < 0
        ):
            raise ValueError(f"{prefix} rate limits must be >= 0")
        if not self.daily_reset_timezone:
            raise ValueError(f"{prefix}_DAILY_RESET_TIMEZONE cannot be empty")
        if not (0 < self.rate_limit_safety_factor <= 1):
            raise ValueError(f"{prefix}_RATE_LIMIT_SAFETY_FACTOR must be in (0, 1]")
        if self.retry_max_delay <= 0:
            raise ValueError(f"{prefix}_RETRY_MAX_DELAY must be > 0")
        if not isinstance(self.json_config, dict):
            raise ValueError(f"{prefix}_JSON_CONFIG must be a JSON object")
        invalid_json_keys = PROTECTED_JSON_CONFIG_KEYS & self.json_config.keys()
        if invalid_json_keys:
            raise ValueError(
                f"{prefix}_JSON_CONFIG cannot override: "
                + ", ".join(sorted(invalid_json_keys))
            )
        positive = {
            "QUEUE_POLL_SECONDS": self.queue_poll_seconds,
            "QUEUE_STALE_SECONDS": self.queue_stale_seconds,
            "QUEUE_WAIT_TIMEOUT_SECONDS": self.queue_wait_timeout_seconds,
            "QUEUE_LEASE_SECONDS": self.queue_lease_seconds,
            "QUEUE_HEARTBEAT_SECONDS": self.queue_heartbeat_seconds,
            "QUEUE_MAINTENANCE_INTERVAL_SECONDS": self.queue_maintenance_interval_seconds,
            "CACHE_TTL_SECONDS": self.cache_ttl_seconds,
            "CACHE_GROUNDED_TTL_SECONDS": self.cache_grounded_ttl_seconds,
            "CACHE_POLL_SECONDS": self.cache_poll_seconds,
            "CACHE_WAIT_TIMEOUT_SECONDS": self.cache_wait_timeout_seconds,
            "CACHE_CLAIM_STALE_SECONDS": self.cache_claim_stale_seconds,
        }
        for suffix, value in positive.items():
            if value <= 0:
                raise ValueError(f"{prefix}_{suffix} must be > 0")
        if self.queue_heartbeat_seconds >= self.queue_lease_seconds:
            raise ValueError(
                f"{prefix}_QUEUE_HEARTBEAT_SECONDS must be lower than "
                f"{prefix}_QUEUE_LEASE_SECONDS"
            )
        for suffix, value in {
            "QUEUE_COMPLETED_RETENTION_DAYS": self.queue_completed_retention_days,
            "QUEUE_FAILED_RETENTION_DAYS": self.queue_failed_retention_days,
            "QUEUE_ABANDONED_RETENTION_DAYS": self.queue_abandoned_retention_days,
        }.items():
            if value < 1:
                raise ValueError(f"{prefix}_{suffix} must be >= 1")
