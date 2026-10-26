from __future__ import annotations

from dataclasses import dataclass

from market_analysis.signals import generate_trend_pullback_candidate


@dataclass(frozen=True, slots=True)
class TrendPullbackStrategy:
    code: str = "TREND_PULLBACK"

    def evaluate(self, snapshot, *, timeframe, observed_at):
        return generate_trend_pullback_candidate(
            snapshot, timeframe=timeframe, observed_at=observed_at
        )
