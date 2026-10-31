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
    save_filing,
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
    assert (
        "UNIQUE NULLS NOT DISTINCT "
        "(event_type, stage, source, event_key, entity_id, revision)" in sql
    )
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


def test_lake_entity_scope_applies_to_fanout_revisions_and_lookups():
    root = Path(__file__).resolve().parents[1] / "oneshot"
    installer = (root / "install_event_lake.sql").read_text()
    cutover = (root / "install_event_lake_cutover.sql").read_text()
    assert "entity_id uuid REFERENCES public.entities(entity_id)" in installer
    assert "ON public.event_lake(entity_id, event_type, stage, occurred_at DESC)" in installer
    assert "IF cardinality(v_entity_ids) > 1 THEN" in installer
    assert "FOREACH v_entity_id IN ARRAY v_entity_ids LOOP" in installer
    assert "jsonb_set(p_envelope, '{subjects}', v_subjects)" in installer
    assert "v_event_id := coalesce(v_event_id, v_child_event_id)" in installer
    assert installer.count("entity_id IS NOT DISTINCT FROM v_entity_id") == 2
    assert "v_source, v_key, v_entity_id)::text" in installer
    assert (
        "SELECT DISTINCT ON (event_type, stage, source, event_key, entity_id)"
        in cutover
    )
    assert cutover.count("root.entity_id IS NOT DISTINCT FROM lake.entity_id") == 2
    assert "original.entity_id IS NOT DISTINCT FROM lake.entity_id" in cutover
    assert "AND score.entity_id = association.entity_id" in cutover
    assert "SELECT DISTINCT ON ((payload->>'news_id')::uuid)" in cutover
    article_writer = cutover.split("v_article_event_id :=", 1)[1].split(
        "SELECT news_entity_id", 1
    )[0]
    assert "jsonb_build_object('entity_id', p_entity_id)" in article_writer
    assert "'subjects', '[]'::jsonb" not in article_writer


def test_news_cutover_preserves_ids_and_writes_only_to_lake():
    sql = (
        Path(__file__).resolve().parents[1] / "oneshot" / "install_event_lake_cutover.sql"
    ).read_text()
    assert "CREATE VIEW public.news AS" in sql
    assert "CREATE VIEW public.news_entities AS" in sql
    assert "coalesce((v_previous.payload->>'news_id')::uuid, gen_random_uuid())" in sql
    for name in ("news", "news_entities"):
        assert f"INSERT INTO public.{name} (" not in sql
        assert f"UPDATE public.{name} " not in sql
    assert "public.sp_save_event(jsonb_build_object(" in sql
    assert "ALTER TABLE public.news SET SCHEMA event_legacy" in sql
    assert "DROP TABLE" not in sql


def test_cutover_projects_all_legacy_archives_and_registers_their_types():
    root = Path(__file__).resolve().parents[1] / "oneshot"
    cutover = (root / "install_event_lake_cutover.sql").read_text()
    installer = (root / "install_event_lake.sql").read_text()
    for table in (
        "news", "news_entities", "news_clusters", "news_impacts", "catalysts",
        "catalyst_history", "filings", "collection_intelligence_events",
    ):
        assert f"CREATE VIEW public.{table} AS" in cutover
    for event_type in (
        "NEWS_ASSOCIATION", "NEWS_CLUSTER", "NEWS_IMPACT", "CATALYST_HISTORY",
    ):
        assert f"('{event_type}', 1," in installer
    assert "AND root.revision = 1" in cutover


def test_common_sql_writer_resolves_issuer_without_creating_incomplete_instruments():
    sql = (
        Path(__file__).resolve().parents[1] / "oneshot" / "install_event_lake.sql"
    ).read_text()
    assert "public.sp_event_resolve_catalyst_entity(" in sql
    assert "public.sp_event_resolve_filing_entity(" in sql
    assert "identity_row.normalized_value = v_cik" in sql
    assert "INSERT INTO public.entity_identities" in sql
    assert "INSERT INTO public.instruments" not in sql
    assert "must resolve to exactly one active entity" in sql
    assert "v_key := v_previous.event_key" in sql
    assert "'catalyst_id', v_previous.payload->'catalyst_id'" in sql
    assert "'filing_id', v_previous.payload->'filing_id'" in sql


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
    assert db.execute.call_count == 2


def test_filing_writes_only_to_lake():
    db = Mock()
    save_filing(db, {
        "source": "SEC", "symbol": "AAPL", "cik": "0000320193",
        "accepted_at": datetime(2026, 10, 31, tzinfo=UTC),
        "filing_date": date(2026, 10, 31), "filing_type": "10-Q",
        "accession_number": "accession", "payload": {},
    })
    db.execute.assert_called_once()
    assert "public.sp_save_event" in str(db.execute.call_args.args[0])
    db.commit.assert_not_called()


def test_intelligence_writes_only_to_lake():
    db = Mock()
    save_intelligence_event(
        db, branch="DAILY_MOVER", event_key="instrument|2026-10-31|2",
        entity_id="entity", instrument_id="instrument", category="mover",
        payload={"dailyChangePct": 2}, evidence={},
    )
    db.execute.assert_called_once()
    assert "public.sp_save_event" in str(db.execute.call_args.args[0])
