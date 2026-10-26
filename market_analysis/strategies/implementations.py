from __future__ import annotations

from dataclasses import dataclass

from market_analysis.models import (
    SignalCandidate,
    SignalDirection,
    SignalType,
)
from market_analysis.signals import classify_trend, generate_trend_pullback_candidate


def candidate(
    code,
    s,
    t,
    d,
    kind,
    confidence,
    reasons,
    observed_at,
    timeframe,
    invalidation=None,
    target=None,
):
    return SignalCandidate(
        code,
        timeframe,
        d,
        kind,
        t,
        s.market_price,
        observed_at,
        max(0, min(confidence, 1)),
        tuple(reasons),
        {"technical": s.to_dict()},
        invalidation,
        target,
    )


@dataclass(frozen=True)
class TrendPullbackStrategy:
    code: str = "TREND_PULLBACK"

    def evaluate(self, snapshot, *, timeframe, observed_at):
        return generate_trend_pullback_candidate(
            snapshot, timeframe=timeframe, observed_at=observed_at
        )


@dataclass(frozen=True)
class SupertrendStrategy:
    code: str = "SUPERTREND"

    def evaluate(self, s, *, timeframe, observed_at):
        if s.supertrend_direction not in (-1, 1):
            return None
        long = s.supertrend_direction == 1
        trend = classify_trend(s)
        return candidate(
            self.code,
            s,
            trend,
            SignalDirection.LONG if long else SignalDirection.SHORT,
            SignalType.BUY if long else SignalType.SELL,
            0.62,
            ["supertrend_bullish" if long else "supertrend_bearish"],
            observed_at,
            timeframe,
            s.supertrend_value,
        )


@dataclass(frozen=True)
class DeepOversoldStrategy:
    code: str = "PULLBACK_DEEP_OVERSOLD"

    def evaluate(self, s, *, timeframe, observed_at):
        if (
            s.rsi is None
            or s.rsi > 30
            or s.drop_from_high_pct is None
            or s.drop_from_high_pct < 8
        ):
            return None
        return candidate(
            self.code,
            s,
            classify_trend(s),
            SignalDirection.LONG,
            SignalType.STRONG_BUY,
            0.7,
            ["rsi_deep_oversold", "material_drop_from_high"],
            observed_at,
            timeframe,
            s.swing_low,
            s.sma_50,
        )


@dataclass(frozen=True)
class SwingMeanReversionStrategy:
    code: str = "CALL_ENTRY_SETUP"

    def evaluate(self, s, *, timeframe, observed_at):
        if (
            s.rsi is None
            or s.rsi > 35
            or not s.bollinger_band_width_pct
            or s.market_price >= s.sma_50
            if s.sma_50
            else True
        ):
            return None
        return candidate(
            self.code,
            s,
            classify_trend(s),
            SignalDirection.LONG,
            SignalType.BUY,
            0.64,
            ["mean_reversion_setup", "price_below_sma50"],
            observed_at,
            timeframe,
            s.swing_low,
            s.sma_50,
        )


@dataclass(frozen=True)
class SwingExitStrategy:
    code: str = "SWING_EXIT"

    def evaluate(self, s, *, timeframe, observed_at):
        if s.rsi is None or s.rsi < 70:
            return None
        return candidate(
            self.code,
            s,
            classify_trend(s),
            SignalDirection.SHORT,
            SignalType.SELL,
            0.6,
            ["rsi_overbought"],
            observed_at,
            timeframe,
            None,
            s.sma_50,
        )


@dataclass(frozen=True)
class SmcChochStrategy:
    code: str = "SMC_CHOCH"

    def evaluate(self, s, *, timeframe, observed_at):
        if s.change_of_character not in (-1, 1):
            return None
        long = s.change_of_character == 1
        return candidate(
            self.code,
            s,
            classify_trend(s),
            SignalDirection.LONG if long else SignalDirection.SHORT,
            SignalType.BUY if long else SignalType.SELL,
            0.68,
            ["change_of_character"],
            observed_at,
            timeframe,
            s.swing_low if long else s.swing_high,
        )


@dataclass(frozen=True)
class SmcBosStrategy:
    code: str = "SMC_BOS"

    def evaluate(self, s, *, timeframe, observed_at):
        if s.break_of_structure not in (-1, 1):
            return None
        long = s.break_of_structure == 1
        return candidate(
            self.code,
            s,
            classify_trend(s),
            SignalDirection.LONG if long else SignalDirection.SHORT,
            SignalType.STRONG_BUY if long else SignalType.STRONG_SELL,
            0.72,
            ["break_of_structure"],
            observed_at,
            timeframe,
            s.bullish_order_block if long else s.bearish_order_block,
        )
