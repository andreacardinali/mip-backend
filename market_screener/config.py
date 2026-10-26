from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScreenerRankingConfig:
    attention_weight: float = 0.20
    technical_weight: float = 0.25
    quant_weight: float = 0.35
    llm_weight: float = 0.20
    minimum_score: float = 0.0
    limit: int = 50

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
