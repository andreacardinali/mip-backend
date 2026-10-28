from dataclasses import dataclass

from market_analysis.models import SignalDirection, SignalType
from market_analysis.strategies.common import build_candidate


def _strong_uptrend(snapshot):
    return (
        snapshot.sma_200 is not None and snapshot.sma_50 is not None
        and snapshot.market_price > snapshot.sma_200
        and snapshot.sma_50 >= snapshot.sma_200
    )


@dataclass(frozen=True, slots=True)
class CallEntryStrategy:
    code: str = "CALL_ENTRY_SETUP"
    maximum_rsi: float = 35.0

    def evaluate(self, snapshot, *, timeframe, observed_at):
        if not _strong_uptrend(snapshot) or snapshot.rsi is None or not 0 < snapshot.rsi <= self.maximum_rsi:
            return None
        if snapshot.bollinger_lower is None or snapshot.market_price >= snapshot.bollinger_lower:
            return None
        return build_candidate(
            code=self.code, snapshot=snapshot, timeframe=timeframe, observed_at=observed_at,
            direction=SignalDirection.LONG, signal_type=SignalType.BUY,
            confidence=0.7, reasons=("strong_uptrend", "below_lower_bollinger", "oversold_rsi"),
            target=snapshot.sma_50, extra_evidence={"action": "ENTRY_LONG"},
        )


@dataclass(frozen=True, slots=True)
class SwingExitStrategy:
    code: str = "SWING_EXIT"
    minimum_rsi: float = 70.0

    def evaluate(self, snapshot, *, timeframe, observed_at):
        if not _strong_uptrend(snapshot) or snapshot.rsi is None or snapshot.rsi < self.minimum_rsi:
            return None
        if snapshot.bollinger_upper is None or snapshot.market_price <= snapshot.bollinger_upper:
            return None
        return build_candidate(
            code=self.code, snapshot=snapshot, timeframe=timeframe, observed_at=observed_at,
            direction=SignalDirection.LONG, signal_type=SignalType.SELL,
            confidence=0.8, reasons=("strong_uptrend", "above_upper_bollinger", "overbought_rsi"),
            extra_evidence={"action": "EXIT_LONG"},
        )