from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from market_analysis.models import SignalCandidate, TechnicalSnapshot
from market_analysis.strategies.base import SignalStrategy
from market_analysis.strategies.implementations import (
    DeepOversoldStrategy,
    SmcBosStrategy,
    SmcChochStrategy,
    SupertrendStrategy,
    SwingExitStrategy,
    SwingMeanReversionStrategy,
    TrendPullbackStrategy,
)


@dataclass(frozen=True)
class StrategyRegistry:
    strategies: tuple[SignalStrategy, ...]

    def evaluate(
        self,
        snapshot: TechnicalSnapshot,
        *,
        timeframe: str,
        observed_at: datetime,
        enabled: tuple[str, ...] | None = None,
    ) -> tuple[SignalCandidate, ...]:
        allowed = {x.upper() for x in enabled} if enabled else None
        out = []
        for strategy in self.strategies:
            if allowed is not None and strategy.code.upper() not in allowed:
                continue
            value = strategy.evaluate(
                snapshot, timeframe=timeframe, observed_at=observed_at
            )
            if value is not None:
                out.append(value)
        return tuple(out)


default_registry = StrategyRegistry(
    (
        TrendPullbackStrategy(),
        SupertrendStrategy(),
        DeepOversoldStrategy(),
        SwingMeanReversionStrategy(),
        SwingExitStrategy(),
        SmcChochStrategy(),
        SmcBosStrategy(),
    )
)
