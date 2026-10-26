from __future__ import annotations

from datetime import datetime

from market_analysis.models import (
    SignalCandidate,
    SignalDirection,
    SignalType,
    TechnicalSnapshot,
    TrendType,
)
from market_analysis.signals import classify_trend


def bounded_confidence(value: float) -> float:
    return round(max(0.0, min(float(value), 1.0)), 6)


def risk_levels(
    snapshot: TechnicalSnapshot,
    direction: SignalDirection,
    *,
    invalidation: float | None = None,
    reward_multiple: float = 2.0,
) -> tuple[float | None, float | None]:
    stop = invalidation
    if stop is None and snapshot.atr and snapshot.atr > 0:
        stop = (
            snapshot.market_price - snapshot.atr * 1.5
            if direction == SignalDirection.LONG
            else snapshot.market_price + snapshot.atr * 1.5
        )
    if stop is None or stop <= 0:
        return None, None
    risk = abs(snapshot.market_price - stop)
    if risk <= 0:
        return stop, None
    target = (
        snapshot.market_price + risk * reward_multiple
        if direction == SignalDirection.LONG
        else snapshot.market_price - risk * reward_multiple
    )
    return round(stop, 8), round(target, 8) if target > 0 else None


def build_candidate(
    *,
    code: str,
    snapshot: TechnicalSnapshot,
    timeframe: str,
    observed_at: datetime,
    direction: SignalDirection,
    signal_type: SignalType,
    confidence: float,
    reasons: tuple[str, ...],
    invalidation: float | None = None,
    target: float | None = None,
    extra_evidence: dict | None = None,
    trend: TrendType | None = None,
) -> SignalCandidate:
    if invalidation is None or target is None:
        derived_stop, derived_target = risk_levels(
            snapshot, direction, invalidation=invalidation
        )
        invalidation = invalidation if invalidation is not None else derived_stop
        target = target if target is not None else derived_target
    evidence = {"technical": snapshot.to_dict()}
    if extra_evidence:
        evidence["strategy"] = extra_evidence
    return SignalCandidate(
        strategy_code=code,
        timeframe=timeframe,
        direction=direction,
        signal_type=signal_type,
        trend=trend or classify_trend(snapshot),
        reference_price=snapshot.market_price,
        observed_at=observed_at,
        confidence=bounded_confidence(confidence),
        reasons=reasons,
        evidence=evidence,
        invalidation_price=invalidation,
        target_price=target,
    )
