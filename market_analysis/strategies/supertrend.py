from __future__ import annotations

from dataclasses import dataclass

from market_analysis.models import SignalDirection, SignalType
from market_analysis.strategies.common import build_candidate


@dataclass(frozen=True, slots=True)
class SupertrendStrategy:
    code: str = "SUPERTREND"

    def evaluate(self, snapshot, *, timeframe, observed_at):
        direction_value = snapshot.supertrend_direction
        if direction_value not in (-1, 1) or not snapshot.supertrend_value:
            return None
        ema = snapshot.ema_50 or snapshot.sma_50
        if ema is None:
            return None
        is_long = direction_value == 1
        aligned = (
            snapshot.market_price > ema if is_long else snapshot.market_price < ema
        )
        if not aligned:
            return None
        direction = SignalDirection.LONG if is_long else SignalDirection.SHORT
        return build_candidate(
            code=self.code,
            snapshot=snapshot,
            timeframe=timeframe,
            observed_at=observed_at,
            direction=direction,
            signal_type=SignalType.BUY if is_long else SignalType.SELL,
            confidence=0.64 + (0.04 if snapshot.sma_200 else 0.0),
            reasons=(
                "supertrend_bullish" if is_long else "supertrend_bearish",
                "price_aligned_with_ema_50",
            ),
            invalidation=snapshot.supertrend_value,
            extra_evidence={"ema50": ema, "direction": direction_value},
        )
