from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, call

import pytest

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
        [selected], [candidate], now=datetime(2026, 10, 29, 12, tzinfo=UTC)
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
        now=datetime(2026, 10, 29, 12, tzinfo=UTC),
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
    now = datetime(2026, 10, 29, 12, tzinfo=UTC)
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
        now=datetime(2026, 10, 29, 12, tzinfo=UTC),
    )
    assert len(matches) == 1
    assert "ANY" in matches[0].message


@pytest.mark.parametrize(
    "alert_type", ["MARKET_SIGNAL", "FIXED_PRICE", "PERCENT_MOVE", "SIGNAL_CHANGE"]
)
@pytest.mark.parametrize("scope", ["SCREENER", "WATCHLIST", "PORTFOLIO", "ALL"])
def test_worker_keeps_rule_candidates_and_notifications_owner_scoped(
    monkeypatch, alert_type, scope
):
    from alerts import service
    from notifications.models import EnqueueResult, EnqueueStatus

    rules = [
        replace(
            rule(scope=scope, threshold=5),
            rule_id=f"rule-{index}",
            user_id=f"user-{index}",
            alert_type=alert_type,
        )
        for index in (1, 2)
    ]
    observed = datetime.now(UTC)
    rows = [
        {
            "instrument_id": f"instrument-{index}",
            "symbol": symbol,
            "signal_id": (
                None
                if alert_type in ("FIXED_PRICE", "PERCENT_MOVE")
                else f"signal-{index}"
            ),
            "composite_score": 86,
            "consensus_score": 78,
            "observed_at": observed.isoformat(),
            "reference_at": (observed - timedelta(days=1)).isoformat(),
            "last_price": 110,
            "reference_price": 100,
            "signal_event_id": f"event-{index}",
            "event_type": "EXPIRED",
        }
        for index, symbol in enumerate(("MSFT", "AAPL"), start=1)
    ]
    repo = MagicMock()
    repo.list_enabled_rules.return_value = rules
    repo.list_rule_candidates.side_effect = [[rows[0]], [rows[1]]]
    repo.register_match.side_effect = ["alert-1", "alert-2"]
    queue = MagicMock()
    queue.enqueue.side_effect = [
        EnqueueResult(EnqueueStatus.CREATED, f"notification-{index}")
        for index in (1, 2)
    ]
    monkeypatch.setattr(service, "AlertRepository", lambda db: repo)
    monkeypatch.setattr(service, "NotificationQueue", lambda db, **kwargs: queue)

    result = service.evaluate_and_enqueue_market_alerts(None, max_candidates=17)

    assert result["candidates"] == result["matches"] == result["enqueued"] == 2
    assert repo.list_rule_candidates.call_args_list == [
        call("rule-1", limit=17),
        call("rule-2", limit=17),
    ]
    for index, (registered, notification) in enumerate(
        zip(
            repo.register_match.call_args_list,
            queue.enqueue.call_args_list,
            strict=True,
        ),
        start=1,
    ):
        assert registered.kwargs["rule_id"] == f"rule-{index}"
        assert registered.kwargs["instrument_id"] == f"instrument-{index}"
        assert registered.kwargs["signal_id"] == rows[index - 1]["signal_id"]
        assert notification.kwargs["user_id"] == f"user-{index}"
        assert notification.kwargs["notification_type"] == alert_type
        assert notification.kwargs["context"]["scope"] == scope
    assert repo.mark_enqueued.call_args_list == [
        call(alert_event_id=f"alert-{index}", notification_id=f"notification-{index}")
        for index in (1, 2)
    ]


@pytest.mark.parametrize("event_id", [None, "alert-1"])
@pytest.mark.parametrize(
    "status", ["DISABLED", "PREFERENCE_DISABLED", "SOURCE_NOT_FOUND", "DUPLICATE"]
)
def test_worker_respects_duplicate_registration_and_skipped_queue(
    monkeypatch, event_id, status
):
    from alerts import service
    from notifications.models import EnqueueResult, EnqueueStatus

    repo = MagicMock()
    repo.list_enabled_rules.return_value = [rule()]
    repo.list_rule_candidates.return_value = [
        {"instrument_id": "instrument-1", "symbol": "MSFT", "composite_score": 86}
    ]
    repo.register_match.return_value = event_id
    queue = MagicMock()
    queue.enqueue.return_value = EnqueueResult(EnqueueStatus(status))
    queue_factory = MagicMock(return_value=queue)
    monkeypatch.setattr(service, "AlertRepository", lambda db: repo)
    monkeypatch.setattr(service, "NotificationQueue", queue_factory)

    result = service.evaluate_and_enqueue_market_alerts(
        None, notifications_enabled=False
    )

    queue_factory.assert_called_once_with(None, enabled=False)
    assert result["duplicates_or_cooldown"] == int(event_id is None)
    assert result["notification_skipped"] == int(event_id is not None)
    assert queue.enqueue.call_count == int(event_id is not None)
    repo.mark_enqueued.assert_not_called()


def test_alert_repository_reads_json_candidates_and_preserves_null_signal():
    from alerts.repository import AlertRepository

    db = MagicMock()
    rows = [{"instrument_id": "instrument-1", "symbol": "MSFT", "signal_id": None}]
    db.execute.return_value.scalars.return_value = rows
    repo = AlertRepository(db)
    assert repo.list_rule_candidates("rule-1", limit=17) == rows
    statement, parameters = db.execute.call_args.args
    assert "sp_alert_list_rule_candidates" in str(statement)
    assert parameters == {"rule_id": "rule-1", "limit": 17}
    db.execute.return_value.scalar.return_value = "alert-1"
    assert (
        repo.register_match(
            rule_id="rule-1",
            instrument_id="instrument-1",
            signal_id=None,
            fingerprint="fingerprint",
            payload={},
        )
        == "alert-1"
    )
    assert db.execute.call_args.args[1]["signal_id"] is None


def test_worker_without_rules_does_not_fetch_candidates_or_create_queue(monkeypatch):
    from alerts import service

    repo = MagicMock()
    repo.list_enabled_rules.return_value = []
    queue_factory = MagicMock()
    monkeypatch.setattr(service, "AlertRepository", lambda db: repo)
    monkeypatch.setattr(service, "NotificationQueue", queue_factory)
    result = service.evaluate_and_enqueue_market_alerts(None)
    assert result["rules"] == result["candidates"] == result["enqueued"] == 0
    repo.list_rule_candidates.assert_not_called()
    queue_factory.assert_not_called()
