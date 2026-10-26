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
