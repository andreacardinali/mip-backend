from datetime import UTC, datetime

from market_analysis.models import (
    ScoreBreakdown,
    SignalCandidate,
    SignalDirection,
    SignalType,
    TrendType,
)
from market_analysis.quant_prefilter import (
    QuantPrefilterConfig,
    prefilter_evaluated_signals,
)
from market_analysis.scan import EvaluatedSignal, ScanMember


def _evaluated(symbol: str, confidence: float) -> EvaluatedSignal:
    return EvaluatedSignal(
        member=ScanMember(
            universe_id="universe",
            instrument_id=f"instrument-{symbol}",
            entity_id=f"entity-{symbol}",
            symbol=symbol,
        ),
        candidate=SignalCandidate(
            strategy_code="TREND_PULLBACK",
            timeframe="1D",
            direction=SignalDirection.LONG,
            signal_type=SignalType.BUY,
            trend=TrendType.BULLISH,
            reference_price=110,
            observed_at=datetime.now(UTC),
            confidence=confidence,
            reasons=("test",),
            evidence={
                "technical": {
                    "rsi": 60,
                    "marketPrice": 110,
                    "sma50": 100,
                    "sma200": 90,
                    "dailyChangePct": 3,
                    "bbWidth": 4,
                }
            },
        ),
        scores=ScoreBreakdown(
            technical=0.8,
            composite=0.8,
        ),
    )


def test_quant_prefilter_sorts_and_limits() -> None:
    results = prefilter_evaluated_signals(
        [_evaluated("BBB", 0.5), _evaluated("AAA", 0.9)],
        config=QuantPrefilterConfig(
            minimum_score=0,
            limit=1,
        ),
    )

    assert len(results) == 1
    assert results[0].evaluated.member.symbol == "AAA"
    assert results[0].quant_score > 0
    assert "trend" in results[0].breakdown
