from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class RankedOpportunity:
    signal_id: str
    instrument_id: str
    symbol: str
    direction: str
    origin: str | None
    attention_score: float | None
    technical_score: float | None
    quant_score: float | None
    llm_score: float | None
    composite_score: float
    ranking_position: int
    verdict: str | None = None
    summary: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    strategy_count: int = 1
    bullish_strategy_count: int = 0
    bearish_strategy_count: int = 0
    consensus_direction: str = "NEUTRAL"
    consensus_score: float = 0.0
    conflict_score: float = 0.0
    supporting_strategies: tuple[str, ...] = ()
    opposing_strategies: tuple[str, ...] = ()
    supporting_signal_ids: tuple[str, ...] = ()
