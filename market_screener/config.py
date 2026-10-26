from __future__ import annotations

from dataclasses import dataclass

from market_screener.constants import (
    MARKET_SCREENER_RANKING_FIELD,
    MARKET_SCREENER_REQUIRE_QUANT,
    MARKET_SCREENER_REQUIRE_TECHNICAL,
    MARKET_SCREENER_WEIGHT_MARKET_CAP,
    MARKET_SCREENER_WEIGHT_QUANT,
    MARKET_SCREENER_WEIGHT_TECHNICAL,
)

@dataclass(frozen=True, slots=True)
class ScreenerRankingConfig:
    technical_weight: float = MARKET_SCREENER_WEIGHT_TECHNICAL
    quant_weight: float = MARKET_SCREENER_WEIGHT_QUANT
    ranking_weight: float = MARKET_SCREENER_WEIGHT_MARKET_CAP
    ranking_field: str = MARKET_SCREENER_RANKING_FIELD
    require_technical: bool = MARKET_SCREENER_REQUIRE_TECHNICAL
    require_quant: bool = MARKET_SCREENER_REQUIRE_QUANT

    def __post_init__(self):
        if self.technical_weight < 0:
            raise ValueError("technical_weight must be non-negative")
        if self.quant_weight < 0:
            raise ValueError("quant_weight must be non-negative")
        if self.ranking_weight < 0:
            raise ValueError("ranking_weight must be non-negative")

        total_weight = (
            self.technical_weight
            + self.quant_weight
            + self.ranking_weight
        )
        if total_weight <= 0:
            raise ValueError("at least one ranking weight must be positive")

        if not self.ranking_field.strip():
            raise ValueError("ranking_field must not be empty")

    @property
    def weight_total(self):
        return (
            self.attention_weight
            + self.technical_weight
            + self.quant_weight
            + self.llm_weight
        )
