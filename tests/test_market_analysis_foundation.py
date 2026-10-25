from datetime import UTC, datetime

import pytest

from market_analysis.models import FinancialSnapshot, TechnicalSnapshot
from market_analysis.scoring import (
    build_score_breakdown,
    score_financial_snapshot,
    score_technical_snapshot,
)
from market_analysis.signals import generate_trend_pullback_candidate


def bullish_snapshot() -> TechnicalSnapshot:
    return TechnicalSnapshot(
        market_price=120.0,
        previous_close=118.0,
        daily_change_pct=1.6949,
        observed_timestamp=1_700_000_000,
        rsi=48.0,
        sma_50=110.0,
        sma_200=100.0,
        bollinger_band_width_pct=8.0,
    )


def test_generates_long_trend_pullback_candidate() -> None:
    candidate = generate_trend_pullback_candidate(
        bullish_snapshot(),
        observed_at=datetime(2026, 10, 23, tzinfo=UTC),
    )

    assert candidate is not None
    assert candidate.direction.value == "LONG"
    assert candidate.strategy_code == "TREND_PULLBACK"
    assert candidate.evidence["technical"]["sma200"] == 100.0


def test_incomplete_snapshot_does_not_emit_candidate() -> None:
    snapshot = TechnicalSnapshot(
        market_price=120.0,
        previous_close=118.0,
        daily_change_pct=1.0,
        observed_timestamp=1_700_000_000,
        rsi=None,
        sma_50=None,
        sma_200=None,
        bollinger_band_width_pct=None,
    )
    assert generate_trend_pullback_candidate(snapshot) is None


def test_scores_are_clamped_and_renormalized() -> None:
    technical = score_technical_snapshot(bullish_snapshot())
    financial = score_financial_snapshot(
        FinancialSnapshot(
            metrics={
                "ROE": 18.0,
                "REVENUE_GROWTH": 12.0,
                "NET_DEBT_EBITDA": 1.2,
            }
        )
    )
    breakdown = build_score_breakdown(
        technical=technical,
        financial=financial,
        catalyst=None,
        llm=None,
    )

    assert 0 <= technical <= 100
    assert financial is not None and 0 <= financial <= 100
    assert breakdown.composite is not None
    assert min(technical, financial) <= breakdown.composite <= max(technical, financial)


def test_candidate_rejects_invalid_confidence() -> None:
    candidate = generate_trend_pullback_candidate(bullish_snapshot())
    assert candidate is not None
    payload = candidate.to_payload()
    assert payload["confidence"] == pytest.approx(candidate.confidence)
