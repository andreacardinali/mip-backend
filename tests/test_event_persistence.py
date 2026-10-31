import json
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from market_time.normalization import TemporalNormalizationError
from services.event_contract import EventEnvelope, stable_event_key
from services.event_persistence import (
    save_catalyst,
    save_event,
    save_intelligence_event,
)


def test_envelope_supports_future_types_and_date_only_events():
    event = EventEnvelope(
        event_type="FUTURE_TYPE",
        stage="ACQUIRED",
        source="provider",
        event_key="event",
        payload={"amount": 1},
        event_day=date(2026, 10, 31),
    ).as_payload()
    assert event["event_type"] == "FUTURE_TYPE"
    assert event["event_day"] == "2026-10-31"
    assert event["occurred_at"] is None
    assert "created_at" not in event
    assert "updated_at" not in event


def test_envelope_rejects_naive_instants_and_nonfinite_json():
    with pytest.raises(TemporalNormalizationError):
        EventEnvelope(
            "NEWS",
            "ACQUIRED",
            "provider",
            "event",
            {},
            occurred_at=datetime(2026, 10, 31),
        ).as_payload()
    with pytest.raises(ValueError):
        EventEnvelope(
            "NEWS", "ACQUIRED", "provider", "event", {"value": float("nan")}
        ).as_payload()


def test_identity_is_stable_and_unambiguous():
    assert stable_event_key({"year": 2026, "quarter": 3}) == stable_event_key(
        {"quarter": 3, "year": 2026}
    )
    assert stable_event_key("a|b", "c") != stable_event_key("a", "b|c")
    assert (
        EventEnvelope(
            "NEWS",
            "ACQUIRED",
            "provider",
            "event",
            {},
            occurred_at=datetime(2026, 10, 31, tzinfo=UTC),
        )
        .as_payload()["occurred_at"]
        .endswith("+00:00")
    )


def test_common_writer_uses_one_sp_and_does_not_own_transaction():
    db = Mock()
    db.execute.return_value.scalar_one.return_value = "event-id"
    assert (
        save_event(db, EventEnvelope("NEWS_ARTICLE", "ACQUIRED", "FMP", "id", {}))
        == "event-id"
    )
    assert "public.sp_save_event" in str(db.execute.call_args.args[0])
    db.execute.assert_called_once()
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_installer_declares_revision_and_timestamp_guards():
    sql = (
        Path(__file__).resolve().parents[1] / "oneshot" / "install_event_lake.sql"
    ).read_text()
    assert "UNIQUE (event_type, stage, source, event_key, revision)" in sql
    assert "pg_advisory_xact_lock" in sql
    assert "BEFORE INSERT OR UPDATE OR DELETE" in sql
    assert "NEW.created_at := v_now" in sql
    assert "NEW.updated_at := v_now" in sql
    assert "SET last_seen_at = clock_timestamp()" in sql
    semantic = sql.split("v_semantic :=", 1)[1].split("v_hash :=", 1)[0]
    for excluded in ("observed_at", "provenance", "raw_payload", "ingested_at"):
        assert excluded not in semantic
    assert sql.startswith("BEGIN;")
    assert sql.rstrip().endswith("COMMIT;")


@pytest.mark.parametrize(
    "invalid",
    [
        {"schema_version": True},
        {"payload_version": False},
        {"payload_version": 1.5},
        {"subjects": ["AAPL"]},
        {"evidence": []},
        {"provenance": None},
        {"raw_payload": []},
        {"parent_event_ids": ["not-a-uuid"]},
        {"event_day": datetime(2026, 10, 31, tzinfo=UTC)},
        {"source_event_id": 0},
    ],
)
def test_envelope_rejects_invalid_shapes(invalid):
    with pytest.raises(ValueError):
        EventEnvelope("NEWS", "ACQUIRED", "provider", "key", {}, **invalid).as_payload()


def _captured_envelopes(db):
    return [
        json.loads(call.args[1]["envelope"])
        for call in db.execute.call_args_list
        if "envelope" in call.args[1]
    ]


def test_upcoming_repeated_scan_keeps_identity_and_semantics():
    db = Mock()
    for remaining in (5, 4):
        save_intelligence_event(
            db,
            branch="UPCOMING_CATALYST",
            event_key=f"scan-{remaining}",
            entity_id="entity",
            instrument_id="instrument",
            category="upcoming",
            payload={
                "eventType": "QUARTERLY_EARNINGS",
                "eventDate": "2026-10-31",
                "daysUntilEvent": remaining,
            },
            evidence={"marketEvidence": {"price": remaining}},
        )
    first, second = _captured_envelopes(db)
    assert first["event_key"] == second["event_key"]
    assert first["payload"] == second["payload"]
    assert first["evidence"] == second["evidence"]
    assert first["provenance"] != second["provenance"]


def test_daily_mover_changes_are_revisions_not_new_identities():
    db = Mock()
    for change, observed in ((2, 1793404800), (3, 1793404801)):
        save_intelligence_event(
            db,
            branch="DAILY_MOVER",
            event_key=f"instrument|2026-10-31|{change}",
            entity_id="entity",
            instrument_id="instrument",
            category="mover",
            payload={
                "dailyChangePct": change,
                "observedDate": observed,
                "marketEvidence": {"run": observed},
            },
            evidence={},
        )
    first, second = _captured_envelopes(db)
    assert first["event_key"] == second["event_key"]
    assert first["payload"] != second["payload"]
    assert first["observed_at"] != second["observed_at"]
    assert "observedDate" not in first["payload"]
    assert "marketEvidence" not in first["payload"]


def test_provider_earnings_identity_survives_rescheduling():
    db = Mock()
    for day in (date(2026, 10, 30), date(2026, 10, 31)):
        save_catalyst(
            db,
            {
                "source": "FMP",
                "symbol": "AAPL",
                "entity_subtype": "QUARTERLY_EARNINGS",
                "entity_date": day,
                "status": "SCHEDULED",
                "importance": "HIGH",
                "title": "Earnings",
                "payload": {"eventId": 0},
            },
        )
    first, second = _captured_envelopes(db)
    assert first["source_event_id"] == "0"
    assert first["event_key"] == second["event_key"]
    assert first["event_day"] != second["event_day"]
    assert first["provenance"]["identity_strategy"] == "provider_id"
