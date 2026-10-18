from dataclasses import dataclass
from config.settings import settings

def _i(name: str, default: int) -> int:
    return int(settings.get(name, default))
def _f(name: str, default: float) -> float:
    return float(settings.get(name, default))
def _b(name: str, default: bool) -> bool:
    return str(settings.get(name, str(default))).lower() in {"1","true","yes","on"}

@dataclass(frozen=True)
class IntelligenceConfig:
    gemini_api_key: str
    gemini_model: str
    gemini_max_retries: int
    ollama_base_url: str
    ollama_model: str
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    send_notifications: bool
    batch_size: int
    upcoming_window_days: int
    breaking_lookback_hours: int
    daily_move_min_pct: float
    daily_renotify_delta_pct: float
    min_history_rows: int
    notification_retention_days: int
    yahoo_range: str
    yahoo_interval: str
    request_timeout: float

    @classmethod
    def load(cls) -> "IntelligenceConfig":
        config = cls(
            gemini_api_key=settings.get("GEMINI_API_KEY", ""),
            gemini_model=settings.get("GEMINI_MODEL", "gemini-3.8-flash"),
            gemini_max_retries=_i("GEMINI_MAX_RETRIES", 4),
            ollama_base_url=settings.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"),
            ollama_model=settings.get("OLLAMA_MODEL", "qwen3:8b"),
            telegram_bot_token=settings.get("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=settings.get("TELEGRAM_CHAT_ID"),
            send_notifications=_b("INTELLIGENCE_SEND_NOTIFICATIONS", False),
            batch_size=_i("INTELLIGENCE_BATCH_SIZE", 4),
            upcoming_window_days=_i("UPCOMING_CATALYST_WINDOW_DAYS", 7),
            breaking_lookback_hours=_i("BREAKING_CATALYST_LOOKBACK_HOURS", 24),
            daily_move_min_pct=_f("DAILY_MOVE_MIN_PCT", 3.0),
            daily_renotify_delta_pct=_f("DAILY_RENOTIFY_DELTA_PCT", 2.0),
            min_history_rows=_i("MIN_HISTORY_ROWS", 50),
            notification_retention_days=_i("NOTIFICATION_RETENTION_DAYS", 45),
            yahoo_range=settings.get("YAHOO_HISTORY_RANGE", "2y"),
            yahoo_interval=settings.get("YAHOO_HISTORY_INTERVAL", "1d"),
            request_timeout=_f("INTELLIGENCE_HTTP_TIMEOUT", 45.0),
        )
        if not config.gemini_api_key:
            raise ValueError("Missing GEMINI_API_KEY")
        if config.batch_size < 1:
            raise ValueError("INTELLIGENCE_BATCH_SIZE must be >= 1")
        if not 1 <= config.upcoming_window_days <= 30:
            raise ValueError("UPCOMING_CATALYST_WINDOW_DAYS must be between 1 and 30")
        return config
