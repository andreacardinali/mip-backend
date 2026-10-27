from dataclasses import dataclass

from config.settings import settings
from llm.config import LLMConfig


def _i(name: str, default: int) -> int:
    return int(settings.get(name, default))


def _f(name: str, default: float) -> float:
    return float(settings.get(name, default))


def _b(name: str, default: bool) -> bool:
    return str(settings.get(name, str(default))).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class IntelligenceConfig:
    research_llm: LLMConfig
    validation_llm: LLMConfig
    create_notifications: bool
    llm_request_max_candidates: int
    upcoming_window_days: int
    breaking_lookback_hours: int
    daily_move_min_pct: float
    min_history_rows: int
    history_range: str
    history_interval: str
    market_evidence_enabled: bool
    news_limit: int
    etf_holding_limit: int
    economic_event_limit: int

    @classmethod
    def load(cls) -> "IntelligenceConfig":
        p = "PORTFOLIO_INTELLIGENCE"
        config = cls(
            research_llm=LLMConfig.from_settings(
                settings,
                prefix=f"{p}_LLM",
                default_provider="gemini",
                default_timeout_seconds=45.0,
            ),
            validation_llm=LLMConfig.from_settings(
                settings,
                prefix=f"{p}_VALIDATION_LLM",
                default_provider="ollama",
                default_timeout_seconds=120.0,
            ),
            create_notifications=_b(f"{p}_NOTIFICATIONS_ENABLED", False),
            llm_request_max_candidates=_i(f"{p}_LLM_REQUEST_MAX_CANDIDATES", 4),
            upcoming_window_days=_i(f"{p}_UPCOMING_CATALYST_WINDOW_DAYS", 7),
            breaking_lookback_hours=_i(f"{p}_BREAKING_CATALYST_LOOKBACK_HOURS", 24),
            daily_move_min_pct=_f(f"{p}_DAILY_MOVE_MIN_PERCENT", 3.0),
            min_history_rows=_i(f"{p}_MINIMUM_HISTORY_ROWS", 50),
            history_range=str(settings.get(f"{p}_HISTORY_RANGE", "2y")),
            history_interval=str(settings.get(f"{p}_HISTORY_INTERVAL", "1d")),
            market_evidence_enabled=_b(f"{p}_MARKET_EVIDENCE_ENABLED", True),
            news_limit=_i(f"{p}_NEWS_LIMIT", 20),
            etf_holding_limit=_i(f"{p}_ETF_HOLDINGS_LIMIT", 25),
            economic_event_limit=_i(f"{p}_ECONOMIC_EVENTS_LIMIT", 50),
        )
        if config.llm_request_max_candidates < 1:
            raise ValueError(f"{p}_LLM_REQUEST_MAX_CANDIDATES must be >= 1")
        if not 1 <= config.upcoming_window_days <= 30:
            raise ValueError(
                f"{p}_UPCOMING_CATALYST_WINDOW_DAYS must be between 1 and 30"
            )
        if (
            min(
                config.news_limit, config.etf_holding_limit, config.economic_event_limit
            )
            < 1
        ):
            raise ValueError("Market evidence limits must be >= 1")
        return config
