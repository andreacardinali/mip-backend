import json
import re
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
from tests.schema_contract import schema_function, schema_sql


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


@pytest.mark.parametrize(
    ("lake", "routine"),
    [
        ("event", "public.sp_save_event"),
        ("analytical", "public.sp_save_analytical_event"),
    ],
)
def test_common_writer_uses_one_sp_and_does_not_own_transaction(lake, routine):
    db = Mock()
    db.execute.return_value.scalar_one.return_value = "event-id"
    assert (
        save_event(
            db, EventEnvelope("NEWS_ARTICLE", "ACQUIRED", "FMP", "id", {}), lake=lake
        )
        == "event-id"
    )
    assert routine in str(db.execute.call_args.args[0])
    db.execute.assert_called_once()
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_common_writer_rejects_unknown_lake_before_querying():
    db = Mock()
    with pytest.raises(ValueError, match="Unknown lake"):
        save_event(db, EventEnvelope("NEWS", "ACQUIRED", "FMP", "id", {}), lake="other")
    db.execute.assert_not_called()


def test_schema_declares_revision_and_timestamp_guards():
    sql = schema_sql()
    assert (
        "UNIQUE NULLS NOT DISTINCT "
        "(event_type, stage, source, event_key, entity_id, revision)" in sql
    )
    writer = schema_function("sp_save_informational_event")
    timestamps = schema_function("event_lake_timestamps")
    assert "pg_advisory_xact_lock" in writer
    assert "BEFORE INSERT OR DELETE OR UPDATE ON public.event_lake" in sql
    assert "NEW.created_at := v_now" in timestamps
    assert "NEW.updated_at := v_now" in timestamps
    assert "SET last_seen_at = clock_timestamp()" in writer
    semantic = writer.split("v_semantic :=", 1)[1].split("v_hash :=", 1)[0]
    for excluded in ("observed_at", "provenance", "raw_payload", "ingested_at"):
        assert excluded not in semantic


def test_lake_entity_scope_applies_to_fanout_revisions_and_lookups():
    sql = schema_sql()
    writer = schema_function("sp_save_informational_event")
    news_writer = schema_function("sp_news_upsert_article")
    assert "FOREIGN KEY (entity_id) REFERENCES public.entities(entity_id)" in sql
    assert (
        "ON public.event_lake USING btree (entity_id, event_type, stage, occurred_at DESC)"
        in sql
    )
    assert "IF cardinality(v_entity_ids) > 1 THEN" in writer
    assert "FOREACH v_entity_id IN ARRAY v_entity_ids LOOP" in writer
    assert "jsonb_set(p_envelope, '{subjects}', v_subjects)" in writer
    assert "v_event_id := coalesce(v_event_id, v_child_event_id)" in writer
    assert writer.count("entity_id IS NOT DISTINCT FROM v_entity_id") == 2
    assert "v_source, v_key, v_entity_id)::text" in writer
    article_writer = news_writer.split("v_article_event_id :=", 1)[1].split(
        "v_association_inserted :=", 1
    )[0]
    assert "jsonb_build_object('entity_id', p_entity_id)" in article_writer
    assert "'subjects', '[]'::jsonb" not in article_writer


def test_news_writer_preserves_ids_and_writes_only_to_lake():
    sql = schema_function("sp_news_upsert_article")
    assert "coalesce((v_previous.payload->>'news_id')::uuid, gen_random_uuid())" in sql
    for name in ("news", "news_entities"):
        assert f"INSERT INTO public.{name} (" not in sql
        assert f"UPDATE public.{name} " not in sql
    assert "public.sp_save_event(jsonb_build_object(" in sql


def test_archive_writers_delegate_to_common_event_writer():
    contracts = {
        "sp_upsert_catalyst": "RETURNS uuid",
        "sp_upsert_filing": "RETURNS uuid",
        "sp_save_intelligence_event": "RETURNS void",
    }
    for name, result in contracts.items():
        body = schema_function(name)
        assert result in body
        assert "public.sp_save_event(" in body
        assert "'parent_event_ids', '[]'::jsonb" in body
        assert "'evidence'," in body
        assert "'provenance'," in body
        assert "INSERT INTO public.catalysts" not in body
        assert "INSERT INTO public.filings" not in body
        assert "INSERT INTO public.collection_intelligence_events" not in body


@pytest.mark.parametrize(
    "name",
    [
        "sp_news_upsert_article",
        "sp_news_update_entity_scores",
        "sp_upsert_catalyst",
        "sp_upsert_filing",
        "sp_save_intelligence_event",
    ],
)
def test_specific_writers_delegate_without_legacy_projection_dependencies(name):
    body = schema_function(name)
    assert "public.sp_save_event(" in body
    assert not re.search(
        r"\b(?:FROM|JOIN|UPDATE|INTO)\s+(?:public\.)?"
        r"(?:news|news_entities|news_clusters|news_impacts|catalysts|"
        r"catalyst_history|filings|collection_intelligence_events)\b",
        body,
        re.IGNORECASE,
    )
    assert not re.search(
        r"\b(?:UPDATE|INSERT INTO|DELETE FROM)\s+(?:public\.)?event_lake\b",
        body,
        re.IGNORECASE,
    )


def test_consolidation_preserves_immutable_entity_references():
    body = schema_function("sp_consolidate_entity")
    assert "SELECT FROM public.event_lake" in body
    assert "WHERE entity_id = p_source_entity_id" in body
    assert "an entity redirect policy is required" in body
    assert "UPDATE public.event_lake" not in body
    assert "DELETE FROM public.event_lake" not in body


def test_event_api_readers_use_stored_routines_not_legacy_relations():
    root = Path(__file__).parents[1]
    for relative_path, routine in (
        ("routers/news.py", "sp_news_get_user_news"),
        ("routers/catalysts.py", "sp_get_user_catalysts"),
        ("routers/entities.py", "sp_get_user_entity_overview"),
        ("services/news_enrichment.py", "sp_news_list_pending_entity_scores"),
        (
            "jobs/portfolio_intelligence/repositories/catalysts.py",
            "sp_get_recent_intelligence_catalysts",
        ),
    ):
        source = (root / relative_path).read_text()
        assert f"public.{routine}(" in source
        assert not re.search(
            r"\b(?:FROM|JOIN)\s+(?:public\.)?(?:news|news_entities|news_clusters|"
            r"news_impacts|catalysts|catalyst_history|filings|collection_intelligence_events)\b",
            source,
            re.IGNORECASE,
        )


def test_common_sql_writer_resolves_issuer_without_creating_incomplete_instruments():
    writer = schema_function("sp_save_informational_event")
    catalyst = schema_function("sp_event_resolve_catalyst_entity")
    filing = schema_function("sp_event_resolve_filing_entity")
    assert "public.sp_event_resolve_catalyst_entity(" in writer
    assert "public.sp_event_resolve_filing_entity(" in writer
    assert "identity_row.normalized_value = v_cik" in filing
    assert "INSERT INTO public.entity_identities" in filing
    for body in (writer, catalyst, filing):
        assert "INSERT INTO public.instruments" not in body
    assert "must resolve to exactly one active entity" in catalyst
    assert "v_key := v_previous.event_key" in writer
    assert "'catalyst_id', v_previous.payload->'catalyst_id'" in writer
    assert "'filing_id', v_previous.payload->'filing_id'" in writer


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
    save_filing(
        db,
        {
            "source": "SEC",
            "symbol": "AAPL",
            "cik": "0000320193",
            "accepted_at": datetime(2026, 10, 31, tzinfo=UTC),
            "filing_date": date(2026, 10, 31),
            "filing_type": "10-Q",
            "accession_number": "accession",
            "payload": {},
        },
    )
    db.execute.assert_called_once()
    assert "public.sp_save_event" in str(db.execute.call_args.args[0])
    db.commit.assert_not_called()


def test_intelligence_writes_only_to_lake():
    db = Mock()
    save_intelligence_event(
        db,
        branch="DAILY_MOVER",
        event_key="instrument|2026-10-31|2",
        entity_id="entity",
        instrument_id="instrument",
        category="mover",
        payload={"dailyChangePct": 2},
        evidence={},
    )
    db.execute.assert_called_once()
    assert "public.sp_save_event" in str(db.execute.call_args.args[0])


def test_lake_cleanup_defaults_to_preview_and_validates_runtime_threshold():
    sql = schema_function("sp_cleanup_event_lake")
    assert "p_threshold_days integer DEFAULT 30" in sql
    assert "p_dry_run boolean DEFAULT true" in sql
    assert "p_threshold_days IS NULL OR p_threshold_days <= 0" in sql
    assert "IF p_dry_run IS NULL THEN" in sql
    assert "make_interval(days => p_threshold_days)" in sql
    assert "IF NOT p_dry_run THEN" in sql
    assert sql.index("IF NOT p_dry_run THEN") < sql.index(
        "DELETE FROM public.event_lake"
    )
    assert "GET DIAGNOSTICS deleted_rows = ROW_COUNT" in sql


def test_lake_cleanup_protects_active_chains_future_events_and_incoming_references():
    sql = schema_function("sp_cleanup_event_lake")
    assert "LOCK TABLE public.event_lake IN SHARE ROW EXCLUSIVE MODE" in sql
    assert "HAVING max(last_seen_at) >= cutoff_at" in sql
    assert "max(event_day) >= (statement_timestamp() AT TIME ZONE 'UTC')::date" in sql
    assert "max(occurred_at) >= statement_timestamp()" in sql
    assert "lake.entity_id IS NOT DISTINCT FROM retained.entity_id" in sql
    assert "constraint_row.confrelid = 'public.event_lake'::regclass" in sql
    assert "LOCK TABLE %I.%I IN SHARE MODE" in sql
    assert "unnest(reference.conkey, reference.confkey)" in sql
    assert "WITH RECURSIVE edges AS" in sql
    assert "unnest(parent_event_ids)" in sql
    assert "SELECT supersedes_event_id, event_id" in sql
    assert "JOIN edges ON edges.event_id = retained.event_id" in sql
    assert "ON CONFLICT DO NOTHING" in sql
    assert "CASCADE" not in sql


def test_lake_cleanup_exception_preserves_immutability_and_transaction_ownership():
    timestamps = schema_function("event_lake_timestamps")
    assert (
        "current_setting('mip.event_lake_cleanup', true) IS DISTINCT FROM 'on'"
        in timestamps
    )
    assert "WHERE candidate.event_id = OLD.event_id" in timestamps
    assert "RETURN OLD;" in timestamps
    body = schema_function("sp_cleanup_event_lake")
    assert "DISABLE TRIGGER" not in body
    assert "SECURITY DEFINER" not in body
    assert "COMMIT" not in body.replace("ON COMMIT DROP", "")
    assert "coalesce(previous_cleanup_setting, '')" in body
    assert "DROP TABLE pg_temp.event_lake_cleanup_candidates" in body


def test_lake_indexes_cover_incoming_foreign_keys_and_latest_ordering():
    sql = schema_sql()
    view = sql.split("CREATE VIEW public.event_lake_latest AS", 1)[1].split(";", 1)[0]
    assert "ON public.event_lake USING btree (supersedes_event_id)" in sql
    ordering = "event_type, stage, source, event_key, entity_id, revision DESC"
    assert f"({ordering});" in sql
    assert f"ORDER BY {ordering}" in view
