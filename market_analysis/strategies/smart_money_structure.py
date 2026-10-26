from __future__ import annotations

from dataclasses import dataclass

from market_analysis.models import SignalDirection, SignalType
from market_analysis.strategies.common import build_candidate


def _confirmation(snapshot, is_long: bool) -> tuple[bool, str | None]:
    order_block = (
        snapshot.bullish_order_block if is_long else snapshot.bearish_order_block
    )
    fair_value_gap = (
        snapshot.bullish_fair_value_gap if is_long else snapshot.bearish_fair_value_gap
    )
    if order_block:
        return True, "order_block"
    if fair_value_gap:
        return True, "fair_value_gap"
    return False, None


def _broken_level(snapshot, is_long: bool, threshold: float) -> bool:
    level = snapshot.swing_high if is_long else snapshot.swing_low
    if not level:
        return False
    return (
        snapshot.market_price > level * (1 + threshold)
        if is_long
        else snapshot.market_price < level * (1 - threshold)
    )


@dataclass(frozen=True, slots=True)
class SmcChochStrategy:
    code: str = "SMC_CHOCH"
    breakout_threshold: float = 0.001

    def evaluate(self, snapshot, *, timeframe, observed_at):
        marker = snapshot.change_of_character
        if marker not in (-1, 1):
            return None
        is_long = marker == 1
        confirmed, source = _confirmation(snapshot, is_long)
        if not confirmed or not _broken_level(
            snapshot, is_long, self.breakout_threshold
        ):
            return None
        direction = SignalDirection.LONG if is_long else SignalDirection.SHORT
        invalidation = snapshot.swing_low if is_long else snapshot.swing_high
        return build_candidate(
            code=self.code,
            snapshot=snapshot,
            timeframe=timeframe,
            observed_at=observed_at,
            direction=direction,
            signal_type=SignalType.BUY if is_long else SignalType.SELL,
            confidence=0.72,
            reasons=("change_of_character", f"confirmed_by_{source}"),
            invalidation=invalidation,
            extra_evidence={"structure": "CHOCH", "confirmation": source},
        )


@dataclass(frozen=True, slots=True)
class SmcBosStrategy:
    code: str = "SMC_BOS"
    breakout_threshold: float = 0.001

    def evaluate(self, snapshot, *, timeframe, observed_at):
        marker = snapshot.break_of_structure
        if marker not in (-1, 1):
            return None
        is_long = marker == 1
        confirmed, source = _confirmation(snapshot, is_long)
        if not confirmed or not _broken_level(
            snapshot, is_long, self.breakout_threshold
        ):
            return None
        direction = SignalDirection.LONG if is_long else SignalDirection.SHORT
        invalidation = (
            snapshot.bullish_order_block or snapshot.swing_low
            if is_long
            else snapshot.bearish_order_block or snapshot.swing_high
        )
        return build_candidate(
            code=self.code,
            snapshot=snapshot,
            timeframe=timeframe,
            observed_at=observed_at,
            direction=direction,
            signal_type=SignalType.STRONG_BUY if is_long else SignalType.STRONG_SELL,
            confidence=0.78,
            reasons=("break_of_structure", f"confirmed_by_{source}"),
            invalidation=invalidation,
            extra_evidence={"structure": "BOS", "confirmation": source},
        )
