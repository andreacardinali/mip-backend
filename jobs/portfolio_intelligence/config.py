from dataclasses import dataclass

from config.settings import settings
from llm.config import LLMConfig


def _i(name: str, default: int) -> int:
    return int(settings.get(name, default))


def _f(name: str, default: float) -> float:
    return float(settings.get(name, default))


def _b(name: str, default: bool) -> bool:
    return str(settings.get(name, str(default))).lower() in {
        "1", "true", "yes", "on"
    }


@dataclass(frozen=True, slots=True)
class IntelligenceConfig:
    research_llm: LLMConfig
    validation_llm: LLMConfig
    create_notifications: bool
    batch_size: int
    upcoming_window_days: int
    breaking_lookback_hours: int
    daily_move_min_pct: float
    min_history_rows: int
    history_range: str
    history_interval: str

    @classmethod
    def load(cls) -> "IntelligenceConfig":
        config = cls(
            research_llm=LLMConfig.from_settings(
                settings,
                prefix="INTELLIGENCE_LLM",
                default_provider="gemini",
                default_timeout=45.0,
            ),
            validation_llm=LLMConfig.from_settings(
                settings,
                prefix="INTELLIGENCE_VALIDATION_LLM",
                default_provider="ollama",
                default_timeout=120.0,
            ),
            create_notifications=_b(
                "INTELLIGENCE_CREATE_NOTIFICATIONS",
                False,
            ),
            batch_size=_i("INTELLIGENCE_BATCH_SIZE", 4),
            upcoming_window_days=_i(
                "UPCOMING_CATALYST_WINDOW_DAYS",
                7,
            ),
            breaking_lookback_hours=_i(
                "BREAKING_CATALYST_LOOKBACK_HOURS",
                24,
            ),
            daily_move_min_pct=_f("DAILY_MOVE_MIN_PCT", 3.0),
            min_history_rows=_i("MIN_HISTORY_ROWS", 50),
            history_range=settings.get("YAHOO_HISTORY_RANGE", "2y"),
            history_interval=settings.get(
                "YAHOO_HISTORY_INTERVAL",
                "1d",
            ),
        )

        if config.batch_size < 1:
            raise ValueError("INTELLIGENCE_BATCH_SIZE must be >= 1")
        if not 1 <= config.upcoming_window_days <= 30:
            raise ValueError(
                "UPCOMING_CATALYST_WINDOW_DAYS must be between 1 and 30"
            )
        if not str(config.history_range or "").strip():
            raise ValueError("YAHOO_HISTORY_RANGE cannot be empty")
        if not str(config.history_interval or "").strip():
            raise ValueError("YAHOO_HISTORY_INTERVAL cannot be empty")

        return config
