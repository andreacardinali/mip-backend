from __future__ import annotations

from datetime import UTC, datetime

from market_analysis.models import (
    SignalCandidate,
    SignalDirection,
    SignalType,
    TechnicalSnapshot,
    TrendType,
)


def classify_trend(snapshot: TechnicalSnapshot) -> TrendType:
    price = snapshot.market_price
    sma_50 = snapshot.sma_50
    sma_200 = snapshot.sma_200

    if sma_50 is None or sma_200 is None:
        return TrendType.SIDEWAYS
    if price > sma_50 > sma_200:
        return TrendType.STRONG_BULLISH
    if price > sma_200:
        return TrendType.BULLISH
    if price < sma_50 < sma_200:
        return TrendType.STRONG_BEARISH
    if price < sma_200:
        return TrendType.BEARISH
    return TrendType.SIDEWAYS


def generate_trend_pullback_candidate(
    snapshot: TechnicalSnapshot,
    *,
    timeframe: str = "1D",
    observed_at: datetime | None = None,
) -> SignalCandidate | None:
    """Generate a deterministic trend-pullback candidate."""
    if snapshot.sma_50 is None or snapshot.rsi is None or snapshot.rsi <= 0:
        return None

    trend = classify_trend(snapshot)
    timestamp = observed_at or datetime.fromtimestamp(snapshot.observed_timestamp, UTC)

    if (
        snapshot.market_price > snapshot.sma_50
        and snapshot.rsi < 70
        and (snapshot.sma_200 is None or snapshot.sma_50 > snapshot.sma_200)
    ):
        confidence = min(1.0, 0.55 + max(0.0, 55.0 - snapshot.rsi) / 100.0)
        return SignalCandidate(
            strategy_code="TREND_PULLBACK",
            timeframe=timeframe,
            direction=SignalDirection.LONG,
            signal_type=SignalType.BUY,
            trend=trend,
            reference_price=snapshot.market_price,
            observed_at=timestamp,
            confidence=confidence,
            reasons=("price_above_sma_50", "rsi_below_70"),
            evidence={"technical": snapshot.to_dict()},
        )

    if (
        snapshot.market_price < snapshot.sma_50
        and snapshot.rsi > 30
        and (snapshot.sma_200 is None or snapshot.sma_50 < snapshot.sma_200)
    ):
        confidence = min(1.0, 0.55 + max(0.0, snapshot.rsi - 45.0) / 100.0)
        return SignalCandidate(
            strategy_code="TREND_PULLBACK",
            timeframe=timeframe,
            direction=SignalDirection.SHORT,
            signal_type=SignalType.SELL,
            trend=trend,
            reference_price=snapshot.market_price,
            observed_at=timestamp,
            confidence=confidence,
            reasons=("price_below_sma_50", "rsi_above_30"),
            evidence={"technical": snapshot.to_dict()},
        )

    return None
