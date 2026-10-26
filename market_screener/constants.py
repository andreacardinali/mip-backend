from __future__ import annotations

from typing import Final

# Market Analysis scan inputs
MARKET_ANALYSIS_PERIOD: Final[str] = "1y"
MARKET_ANALYSIS_INTERVAL: Final[str] = "1d"
MARKET_ANALYSIS_MINIMUM_BARS: Final[int] = 200
MARKET_ANALYSIS_TIMEFRAME: Final[str] = "1D"

# Discovery and quantitative prefilter
MARKET_ANALYSIS_QUANT_MIN_SCORE: Final[float] = 0.0
MARKET_ANALYSIS_QUANT_CANDIDATE_LIMIT: Final[int] = 25
MARKET_ANALYSIS_CORE_MIN_ATTENTION_SCORE: Final[float] = 20.0
MARKET_ANALYSIS_DISCOVERY_MIN_ATTENTION_SCORE: Final[float] = 40.0
MARKET_ANALYSIS_CORE_CANDIDATE_LIMIT: Final[int] = 100
MARKET_ANALYSIS_DISCOVERY_CANDIDATE_LIMIT: Final[int] = 25

# Ranking score boundaries
SCORE_MINIMUM: Final[float] = 0.0
SCORE_MAXIMUM: Final[float] = 100.0

# Single ranking formula
ATTENTION_WEIGHT: Final[float] = 0.20
TECHNICAL_WEIGHT: Final[float] = 0.25
QUANT_WEIGHT: Final[float] = 0.35
LLM_WEIGHT: Final[float] = 0.20

RANKING_WEIGHT_TOTAL: Final[float] = (
    ATTENTION_WEIGHT + TECHNICAL_WEIGHT + QUANT_WEIGHT + LLM_WEIGHT
)

# Technical and quantitative scores are mandatory.
REQUIRE_TECHNICAL_SCORE: Final[bool] = True
REQUIRE_QUANT_SCORE: Final[bool] = True

# Optional components use a neutral value instead of redistributing weights.
NEUTRAL_ATTENTION_SCORE: Final[float] = 50.0
NEUTRAL_LLM_SCORE: Final[float] = 50.0

# Multi-strategy consensus
MINIMUM_STRATEGIES_FOR_CONSENSUS: Final[int] = 2
CONSENSUS_BONUS_MAXIMUM: Final[float] = 8.0
CONFLICT_PENALTY_MAXIMUM: Final[float] = 12.0

# Operational ranking limits
MARKET_SCREENER_MINIMUM_SCORE: Final[float] = 0.0
MARKET_SCREENER_LIMIT: Final[int] = 50
RANKING_INPUT_LIMIT_MULTIPLIER: Final[int] = 8
RANKING_INPUT_MINIMUM_LIMIT: Final[int] = 200

CONSENSUS_DIRECTION_LONG: Final[str] = "LONG"
CONSENSUS_DIRECTION_SHORT: Final[str] = "SHORT"
CONSENSUS_DIRECTION_CONFLICT: Final[str] = "CONFLICT"
CONSENSUS_DIRECTION_NEUTRAL: Final[str] = "NEUTRAL"

SIGNAL_DIRECTION_LONG: Final[str] = "LONG"
SIGNAL_DIRECTION_SHORT: Final[str] = "SHORT"

__all__ = [name for name in globals() if name.isupper()]
