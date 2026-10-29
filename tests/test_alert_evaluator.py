from alerts.evaluator import evaluate_alerts
from alerts.models import AlertCandidate, AlertRule
from dataclasses import replace
from datetime import datetime, timezone
import pytest


def rule(**c):
    return AlertRule(
        "rule-1", "user-1", "High conviction", "MARKET_SIGNAL", "HIGH", None, c, 240
    )


def item(**o):
    v = dict(
        signal_id="signal-1",
        instrument_id="instrument-1",
        symbol="MSFT",
        direction="BUY",
        origin="DISCOVERY",
        composite_score=0.86,
        consensus_score=0.78,
        conflict_score=0.12,
        strategy_count=3,
        verdict="STRONG_BUY",
        summary="Conferma.",
        evidence={},
    )
    v.update(o)
    return AlertCandidate(**v)


def test_match():
    assert (
        len(
            evaluate_alerts(
                [
                    rule(
                        min_composite_score=0.8,
                        directions=["BUY"],
                        min_strategy_count=2,
                    )
                ],
                [item()],
            )
        )
        == 1
    )


def test_conflict_rejected():
    assert (
        evaluate_alerts([rule(max_conflict_score=0.2)], [item(conflict_score=0.5)])
        == []
    )


def test_strategy_filter_is_case_insensitive_and_matches_any_selected_strategy():
    matches = evaluate_alerts(
        [rule(strategy_codes=[" call_entry_setup ", "SUPERTREND"])],
        [item(strategy_codes=("CALL_ENTRY_SETUP", "TREND_PULLBACK"))],
    )
    assert len(matches) == 1
    assert matches[0].context["strategy_codes"] == [
        "CALL_ENTRY_SETUP",
        "TREND_PULLBACK",
    ]


def test_strategy_filter_rejects_missing_or_different_strategies():
    selected = rule(strategy_codes=["CALL_ENTRY_SETUP"])
    assert evaluate_alerts([selected], [item()]) == []
    assert evaluate_alerts([selected], [item(strategy_codes=("SWING_EXIT",))]) == []


def test_fingerprint_changes():
    assert (
        evaluate_alerts([rule()], [item(direction="BUY")])[0].fingerprint
        != evaluate_alerts([rule()], [item(direction="SELL")])[0].fingerprint
    )


def test_ranked_candidate_normalizes_scores():
    from alerts.service import _candidate

    candidate = _candidate(
        dict(
            signal_id="signal-1",
            instrument_id="instrument-1",
            symbol="MSFT",
            direction="BUY",
            composite_score=86,
            consensus_score=78,
            conflict_score=12,
            strategy_count=3,
        )
    )
    assert candidate.composite_score == 0.86
    assert candidate.consensus_score == 0.78
    assert candidate.conflict_score == 0.12
    assert len(evaluate_alerts([rule(min_composite_score=0.8)], [candidate])) == 1
    assert evaluate_alerts([rule(max_conflict_score=0.1)], [candidate]) == []


def test_ranked_candidate_preserves_strategy_codes():
    from alerts.service import _candidate

    candidate = _candidate(
        {
            "signal_id": "signal",
            "instrument_id": "instrument",
            "symbol": "MSFT",
            "composite_score": 86,
            "consensus_score": 78,
            "conflict_score": 12,
            "strategy_codes": ["CALL_ENTRY_SETUP", "TREND_PULLBACK"],
        }
    )
    assert candidate.strategy_codes == ("CALL_ENTRY_SETUP", "TREND_PULLBACK")
    assert (
        len(evaluate_alerts([rule(strategy_codes=["CALL_ENTRY_SETUP"])], [candidate]))
        == 1
    )
    assert evaluate_alerts([rule(strategy_codes=["SWING_EXIT"])], [candidate]) == []


@pytest.mark.parametrize(
    "alert_type,direction,price,expected",
    [
        ("FIXED_PRICE", "UP", 105, True),
        ("FIXED_PRICE", "UP", 99, False),
        ("FIXED_PRICE", "DOWN", 99, True),
        ("PERCENT_MOVE", "UP", 106, True),
        ("PERCENT_MOVE", "DOWN", 94, True),
        ("PERCENT_MOVE", "ANY", 94, True),
        ("PERCENT_MOVE", "UP", 101, False),
    ],
)
def test_price_monitoring_thresholds(alert_type, direction, price, expected):
    selected = replace(
        rule(
            threshold=100 if alert_type == "FIXED_PRICE" else 5,
            price_direction=direction,
        ),
        alert_type=alert_type,
    )
    candidate = item(
        signal_id=None,
        last_price=price,
        reference_price=100,
        reference_at="2026-10-28T16:00:00+00:00",
        observed_at="2026-10-29T12:00:00+00:00",
    )
    matches = evaluate_alerts(
        [selected], [candidate], now=datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    )
    assert bool(matches) == expected
    if matches:
        assert matches[0].context["signal_id"] is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"last_price": None},
        {"last_price": float("nan")},
        {"observed_at": None},
        {"observed_at": "2026-10-28T12:00:00+00:00"},
        {"observed_at": "2026-10-30T12:00:00+00:00"},
        {"observed_at": "2026-10-29T12:00:00"},
        {"reference_price": 0},
        {"reference_at": "2026-09-01T12:00:00+00:00"},
    ],
)
def test_missing_or_stale_price_is_not_an_alert(overrides):
    selected = replace(
        rule(threshold=5, price_direction="ANY"), alert_type="PERCENT_MOVE"
    )
    candidate = item(
        last_price=120,
        reference_price=100,
        observed_at="2026-10-29T12:00:00+00:00",
        reference_at="2026-10-28T16:00:00+00:00",
    )
    assert not evaluate_alerts(
        [selected],
        [replace(candidate, **overrides)],
        now=datetime(2026, 10, 29, 12, tzinfo=timezone.utc),
    )


def test_lifecycle_exits_do_not_require_active_opportunity_scores():
    selected = replace(rule(strategy_codes=["SWING_EXIT"]), alert_type="SIGNAL_CHANGE")
    candidate = item(
        composite_score=0,
        signal_event_id="event-1",
        event_type="EXPIRED",
        observed_at="2026-10-29T12:00:00+00:00",
        strategy_codes=("SWING_EXIT",),
    )
    now = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    matches = evaluate_alerts([selected], [candidate], now=now)
    assert len(matches) == 1
    assert (
        matches[0].fingerprint
        != evaluate_alerts(
            [selected], [replace(candidate, signal_event_id="event-2")], now=now
        )[0].fingerprint
    )
    assert not evaluate_alerts(
        [replace(selected, conditions={"excluded_symbols": ["MSFT"]})],
        [candidate],
        now=now,
    )


def test_price_candidate_mapping_and_default_percent_direction():
    from alerts.service import _candidate

    candidate = _candidate(
        {
            "signal_id": None,
            "instrument_id": "instrument-1",
            "symbol": "MSFT",
            "last_price": "94",
            "reference_price": "100",
            "observed_at": "2026-10-29T12:00:00+00:00",
            "reference_at": "2026-10-28T16:00:00+00:00",
        }
    )
    assert candidate.signal_id is None
    matches = evaluate_alerts(
        [replace(rule(threshold=5), alert_type="PERCENT_MOVE")],
        [candidate],
        now=datetime(2026, 10, 29, 12, tzinfo=timezone.utc),
    )
    assert len(matches) == 1
    assert "ANY" in matches[0].message
