from __future__ import annotations

from dataclasses import dataclass

from market_screener.constants import (
    ATTENTION_WEIGHT,
    CONFLICT_PENALTY_MAXIMUM,
    CONSENSUS_BONUS_MAXIMUM,
    LLM_WEIGHT,
    MARKET_SCREENER_LIMIT,
    MARKET_SCREENER_MINIMUM_SCORE,
    QUANT_WEIGHT,
    REQUIRE_QUANT_SCORE,
    REQUIRE_TECHNICAL_SCORE,
    TECHNICAL_WEIGHT,
)


@dataclass(frozen=True, slots=True)
class ScreenerRankingConfig:
    attention_weight: float = ATTENTION_WEIGHT
    technical_weight: float = TECHNICAL_WEIGHT
    quant_weight: float = QUANT_WEIGHT
    llm_weight: float = LLM_WEIGHT
    consensus_bonus: float = CONSENSUS_BONUS_MAXIMUM
    conflict_penalty: float = CONFLICT_PENALTY_MAXIMUM
    require_technical: bool = REQUIRE_TECHNICAL_SCORE
    require_quant: bool = REQUIRE_QUANT_SCORE
    minimum_score: float = MARKET_SCREENER_MINIMUM_SCORE
    limit: int = MARKET_SCREENER_LIMIT

    def __post_init__(self) -> None:
        weights = (
            self.attention_weight,
            self.technical_weight,
            self.quant_weight,
            self.llm_weight,
        )
        if any(weight < 0 for weight in weights):
            raise ValueError("ranking weights cannot be negative")
        if sum(weights) <= 0:
            raise ValueError("at least one ranking weight must be positive")
        if self.consensus_bonus < 0 or self.conflict_penalty < 0:
            raise ValueError("consensus adjustments cannot be negative")
        if not 0 <= self.minimum_score <= 100:
            raise ValueError("minimum_score must be between 0 and 100")
        if self.limit < 1:
            raise ValueError("limit must be positive")

    @property
    def weight_total(self) -> float:
        return (
            self.attention_weight
            + self.technical_weight
            + self.quant_weight
            + self.llm_weight
        )
