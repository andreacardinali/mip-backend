from dataclasses import replace
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


def test_budget_is_per_instrument_and_preserves_selected_strategies():
    first = _evaluated("AAA", 0.9)
    second = replace(first, candidate=replace(first.candidate, strategy_code="SUPERTREND", confidence=0.1))
    exit_signal = replace(first, candidate=replace(first.candidate, strategy_code="SWING_EXIT"))
    results = prefilter_evaluated_signals([first, second, exit_signal, _evaluated("BBB", 0.5)], config=QuantPrefilterConfig(limit=1))
    assert len(results) == 2
    assert {result.evaluated.candidate.strategy_code for result in results} == {"TREND_PULLBACK", "SUPERTREND"}
    assert {result.evaluated.member.instrument_id for result in results} == {first.member.instrument_id}
