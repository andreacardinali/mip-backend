from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AlertRule:
    rule_id: str
    user_id: str
    name: str
    alert_type: str
    severity: str
    channel: str | None
    conditions: dict[str, Any] = field(default_factory=dict)
    cooldown_minutes: int = 240


@dataclass(frozen=True, slots=True)
class AlertCandidate:
    signal_id: str
    instrument_id: str
    symbol: str
    direction: str
    origin: str | None
    composite_score: float
    consensus_score: float
    conflict_score: float
    strategy_count: int
    verdict: str | None
    summary: str | None
    evidence: dict[str, Any] = field(default_factory=dict)
    strategy_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AlertMatch:
    rule: AlertRule
    candidate: AlertCandidate
    fingerprint: str
    title: str
    message: str
    context: dict[str, Any]
