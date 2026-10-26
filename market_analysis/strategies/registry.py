from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from market_analysis.models import SignalCandidate, TechnicalSnapshot
from market_analysis.strategies.base import SignalStrategy
from market_analysis.strategies.deep_oversold import DeepOversoldStrategy
from market_analysis.strategies.smart_money_structure import (
    SmcBosStrategy,
    SmcChochStrategy,
)
from market_analysis.strategies.supertrend import SupertrendStrategy
from market_analysis.strategies.trend_pullback import TrendPullbackStrategy


@dataclass(frozen=True, slots=True)
class StrategyRegistry:
    strategies: tuple[SignalStrategy, ...]

    def __post_init__(self):
        codes = [strategy.code.upper() for strategy in self.strategies]
        duplicates = sorted({code for code in codes if codes.count(code) > 1})
        if duplicates:
            raise ValueError("duplicate strategy codes: " + ", ".join(duplicates))

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(strategy.code for strategy in self.strategies)

    def evaluate(
        self,
        snapshot: TechnicalSnapshot,
        *,
        timeframe: str,
        observed_at: datetime,
        enabled: tuple[str, ...] | None = None,
    ) -> tuple[SignalCandidate, ...]:
        allowed = {value.strip().upper() for value in enabled} if enabled else None
        if allowed is not None:
            unknown = sorted(allowed - {code.upper() for code in self.codes})
            if unknown:
                raise ValueError("unknown enabled strategies: " + ", ".join(unknown))
        candidates = []
        for strategy in self.strategies:
            if allowed is not None and strategy.code.upper() not in allowed:
                continue
            candidate = strategy.evaluate(
                snapshot, timeframe=timeframe, observed_at=observed_at
            )
            if candidate is not None:
                candidates.append(candidate)
        return tuple(candidates)


default_registry = StrategyRegistry(
    (
        TrendPullbackStrategy(),
        SupertrendStrategy(),
        DeepOversoldStrategy(),
        SmcChochStrategy(),
        SmcBosStrategy(),
    )
)
