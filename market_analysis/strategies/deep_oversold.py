from __future__ import annotations

from dataclasses import dataclass

from market_analysis.models import SignalDirection, SignalType
from market_analysis.strategies.common import build_candidate


@dataclass(frozen=True, slots=True)
class DeepOversoldStrategy:
    code: str = "PULLBACK_DEEP_OVERSOLD"
    maximum_rsi: float = 20.0
    minimum_drop_pct: float = 8.0

    def evaluate(self, snapshot, *, timeframe, observed_at):
        if snapshot.rsi is None or snapshot.rsi > self.maximum_rsi:
            return None
        if (
            snapshot.drop_from_high_pct is None
            or snapshot.drop_from_high_pct < self.minimum_drop_pct
        ):
            return None
        confidence = 0.68 + min(0.12, (self.maximum_rsi - snapshot.rsi) / 100)
        return build_candidate(
            code=self.code,
            snapshot=snapshot,
            timeframe=timeframe,
            observed_at=observed_at,
            direction=SignalDirection.LONG,
            signal_type=SignalType.STRONG_BUY,
            confidence=confidence,
            reasons=("rsi_deep_oversold", "material_drop_from_high"),
            invalidation=snapshot.swing_low,
            target=snapshot.sma_50,
            extra_evidence={
                "rsiThreshold": self.maximum_rsi,
                "minimumDropPct": self.minimum_drop_pct,
            },
        )
