from alerts.evaluator import evaluate_alerts
from alerts.models import AlertCandidate, AlertRule


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
        "CALL_ENTRY_SETUP", "TREND_PULLBACK"
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
    assert len(evaluate_alerts([rule(strategy_codes=["CALL_ENTRY_SETUP"])], [candidate])) == 1
    assert evaluate_alerts([rule(strategy_codes=["SWING_EXIT"])], [candidate]) == []
