from dataclasses import dataclass
from config.settings import settings


def _i(name: str, default: int) -> int:
    return int(settings.get(name, default))


def _f(name: str, default: float) -> float:
    return float(settings.get(name, default))


def _b(name: str, default: bool) -> bool:
    return str(settings.get(name, str(default))).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class IntelligenceConfig:
    llm_provider: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_max_retries: int
    llm_timeout: float
    validation_llm_provider: str
    validation_llm_base_url: str
    validation_llm_api_key: str
    validation_llm_model: str
    validation_llm_max_retries: int
    validation_llm_timeout: float
    send_notifications: bool
    batch_size: int
    upcoming_window_days: int
    breaking_lookback_hours: int
    daily_move_min_pct: float
    daily_renotify_delta_pct: float
    min_history_rows: int
    notification_http_timeout: float
    notification_retention_days: int
    yahoo_range: str
    yahoo_interval: str

    @classmethod
    def load(cls) -> "IntelligenceConfig":
        config = cls(
            llm_provider=settings.get("INTELLIGENCE_LLM_PROVIDER", "gemini"),
            llm_base_url=settings.get("INTELLIGENCE_LLM_BASE_URL", "").rstrip("/"),
            llm_api_key=settings.get("INTELLIGENCE_LLM_API_KEY", ""),
            llm_model=settings.get("INTELLIGENCE_LLM_MODEL", ""),
            llm_max_retries=_i("INTELLIGENCE_LLM_MAX_RETRIES", 4),
            llm_timeout=_f("INTELLIGENCE_LLM_TIMEOUT", 45.0),
            validation_llm_provider=settings.get("INTELLIGENCE_VALIDATION_LLM_PROVIDER", "ollama"),
            validation_llm_base_url=settings.get("INTELLIGENCE_VALIDATION_LLM_BASE_URL", "").rstrip("/"),
            validation_llm_api_key=settings.get("INTELLIGENCE_VALIDATION_LLM_API_KEY", ""),
            validation_llm_model=settings.get("INTELLIGENCE_VALIDATION_LLM_MODEL", ""),
            validation_llm_max_retries=_i("INTELLIGENCE_VALIDATION_LLM_MAX_RETRIES", 4),
            validation_llm_timeout=_f("INTELLIGENCE_VALIDATION_LLM_TIMEOUT", 120),
            send_notifications=_b("INTELLIGENCE_SEND_NOTIFICATIONS", False),
            batch_size=_i("INTELLIGENCE_BATCH_SIZE", 4),
            upcoming_window_days=_i("UPCOMING_CATALYST_WINDOW_DAYS", 7),
            breaking_lookback_hours=_i("BREAKING_CATALYST_LOOKBACK_HOURS", 24),
            daily_move_min_pct=_f("DAILY_MOVE_MIN_PCT", 3.0),
            daily_renotify_delta_pct=_f("DAILY_RENOTIFY_DELTA_PCT", 2.0),
            min_history_rows=_i("MIN_HISTORY_ROWS", 50),
            notification_timeout=_f("INTELLIGENCE_NOTIFICATION_TIMEOUT", 30.0),
            notification_retention_days=_i("NOTIFICATION_RETENTION_DAYS", 45),
            yahoo_range=settings.get("YAHOO_HISTORY_RANGE", "2y"),
            yahoo_interval=settings.get("YAHOO_HISTORY_INTERVAL", "1d"),
        )
        research_llm = config.llm_provider.strip().lower()
        validation_lln = config.validation_llm_provider.strip().lower()

        if research_llm not in {
            "gemini",
            "ollama",
            "openai_compatible",
        }:
            raise ValueError(
                "Unsupported "
                "INTELLIGENCE_LLM_PROVIDER: "
                f"{config.llm_provider}"
            )
            
        if research_llm in { "openai_compatible", "ollama"}:
            if not config.llm_base_url:
                raise ValueError(
                    "Missing INTELLIGENCE_LLM_BASE_URL"
                )

        if validation_llm not in {
            "gemini",
            "ollama",
            "openai_compatible",
        }:
            raise ValueError(
                "Unsupported "
                "INTELLIGENCE_VALIDATION_LLM_PROVIDER: "
                f"{config.llm_provider}"
            )
            
        if validation_llm in { "openai_compatible", "ollama"}:
            if not config.llm_base_url:
                raise ValueError(
                    "Missing INTELLIGENCE_VALIDATION_LLM_BASE_URL"
                )

        if config.llm_max_retries < 0:
            raise ValueError(
                "INTELLIGENCE_LLM_MAX_RETRIES "
                "must be >= 0"
            )

        if config.validation_llm_max_retries < 0:
            raise ValueError(
                "INTELLIGENCE_VALIDATION_LLM_MAX_RETRIES "
                "must be >= 0"
            )

        if not config.llm_model:
            raise ValueError(
                "Missing INTELLIGENCE_LLM_MODEL"
            )
            
       if not config.validation_llm_model:
            raise ValueError(
                "Missing INTELLIGENCE_VALIDATION_LLM_MODEL"
            )

        if config.llm_max_retries < 0:
            raise ValueError(
                "INTELLIGENCE_LLM_MAX_RETRIES "
                "must be >= 0"
            )
            
        if config.validation_llm_max_retries < 0:
            raise ValueError(
                "INTELLIGENCE_VALIDATION_LLM_MAX_RETRIES "
                "must be >= 0"
            )
        if config.batch_size < 1:
            raise ValueError("INTELLIGENCE_BATCH_SIZE must be >= 1")
        if not 1 <= config.upcoming_window_days <= 30:
            raise ValueError("UPCOMING_CATALYST_WINDOW_DAYS must be between 1 and 30")
        return config
