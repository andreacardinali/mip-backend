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
    assert sql.removeprefix("ROLLBACK;\n\n").startswith("BEGIN;")
    assert sql.rstrip().endswith("COMMIT;")


def test_installer_upgrades_only_empty_legacy_lake_before_entity_index():
    sql = (
        Path(__file__).resolve().parents[1] / "oneshot" / "install_event_lake.sql"
    ).read_text()
    upgrade = sql.split("DO $$", 1)[1].split("CREATE INDEX", 1)[0]
    assert "LOCK TABLE public.event_lake IN ACCESS EXCLUSIVE MODE" in upgrade
    assert "pg_catalog.pg_attribute" in upgrade
    assert "pg_catalog.pg_constraint" in upgrade
    assert "constraint_row.conkey" in upgrade
    assert "pg_catalog.pg_index" in upgrade
    assert "index_row.indexrelid = constraint_row.conindid" in upgrade
    assert "index_row.indnullsnotdistinct" in upgrade
    guard = upgrade.index("IF EXISTS (SELECT FROM public.event_lake)")
    add_column = upgrade.index("ADD COLUMN entity_id uuid REFERENCES")
    drop_constraint = upgrade.index("DROP CONSTRAINT %I")
    add_constraint = upgrade.index("ADD CONSTRAINT event_lake_entity_revision_key")
    assert guard < add_column < drop_constraint < add_constraint
    assert "requires an entity-scoped data migration" in upgrade
    assert "IF NOT v_entity_exists THEN" in upgrade
    assert "IF NOT v_has_entity_unique THEN" in upgrade
    assert "FOREACH v_constraint_name IN ARRAY v_old_constraints LOOP" in upgrade
    assert "UPDATE public.event_lake" not in upgrade
    assert "DELETE FROM" not in upgrade


def test_populated_lake_migration_preserves_ids_and_rebuilds_entity_chains():
    sql = (
        Path(__file__).resolve().parents[1]
        / "oneshot" / "migrate_event_lake_entity_scope.sql"
    ).read_text()
    assert sql.startswith("BEGIN;")
    assert sql.rstrip().endswith("COMMIT;")
    assert "LOCK TABLE public.event_lake IN ACCESS EXCLUSIVE MODE" in sql
    assert "CREATE TABLE event_lake_entity_migration.original AS TABLE" in sql
    assert "CREATE TABLE event_lake_entity_migration.event_map" in sql
    assert "THEN event_id ELSE gen_random_uuid()" in sql
    assert "mapping.entity_id" in sql
    assert "lag(mapping.migrated_event_id) OVER" in sql
    assert "same_entity.entity_id IS NOT DISTINCT FROM mapping.entity_id" in sql
    assert "'subjects', plan.migrated_subjects" in sql
    assert "'parent_event_ids', plan.migrated_parents" in sql
    assert "UNIQUE NULLS NOT DISTINCT (event_type, stage, source, event_key, entity_id, revision)" in sql
    assert sql.index("DISABLE TRIGGER") < sql.index("UPDATE public.event_lake")
    assert "pg_catalog.pg_get_constraintdef(oid)" in sql
    assert "confrelid = 'public.event_lake'::regclass AND contype = 'f'" in sql
    assert sql.index("VALIDATE CONSTRAINT %I") > sql.index("INSERT INTO public.event_lake")
    assert sql.index("timestamp preservation failed") < sql.index("ENABLE TRIGGER")
    for timestamp in ("created_at", "updated_at", "ingested_at", "last_seen_at"):
        assert f"lake.{timestamp} IS DISTINCT FROM plan.{timestamp}" in sql
    assert "External foreign keys require a reviewed multi-entity mapping" in sql
    assert "Existing revision chains are inconsistent" in sql
    assert "DELETE FROM" not in sql
    assert "DROP TABLE" not in sql
    assert "sp_save_event" not in sql


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
        "v_association_inserted :=", 1
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


def test_cutover_requires_locked_unchanged_backfill_before_replacing_tables():
    sql = (
        Path(__file__).resolve().parents[1] / "oneshot" / "install_event_lake_cutover.sql"
    ).read_text()
    preflight = sql.split("CREATE VIEW public.event_lake_latest AS", 1)[0]
    assert "SET LOCAL lock_timeout = '10s'" in preflight
    assert "LOCK TABLE public.event_lake IN ACCESS EXCLUSIVE MODE" in preflight
    assert "snapshot.row_data = current_rows.row_data" in preflight
    assert "JOIN public.event_lake lake ON lake.event_id = mapping.event_id" in preflight
    assert "Legacy data changed or backfill incomplete" in preflight
    assert "Cutover already installed" in preflight


def test_cutover_replaces_all_legacy_archive_writers():
    sql = (Path(__file__).parents[1] / "oneshot" / "install_event_lake_cutover.sql").read_text()
    contracts = {
        "sp_upsert_catalyst": "RETURNS uuid",
        "sp_upsert_filing": "RETURNS uuid",
        "sp_save_intelligence_event": "RETURNS void",
    }
    for name, result in contracts.items():
        body = sql.split(f"CREATE OR REPLACE FUNCTION public.{name}(", 1)[1].split("$$;", 1)[0]
        assert result in body
        assert "public.sp_save_event(" in body
        assert "'parent_event_ids', '[]'::jsonb" in body
        assert "'evidence'," in body
        assert "'provenance'," in body
        assert "INSERT INTO public.catalysts" not in body
        assert "INSERT INTO public.filings" not in body
        assert "INSERT INTO public.collection_intelligence_events" not in body


@pytest.mark.parametrize("name", [
    "sp_news_upsert_article", "sp_news_update_entity_scores",
    "sp_upsert_catalyst", "sp_upsert_filing", "sp_save_intelligence_event",
])
def test_specific_writers_delegate_without_legacy_projection_dependencies(name):
    sql = (Path(__file__).parents[1] / "oneshot" / "install_event_lake_cutover.sql").read_text()
    body = sql.split(f"CREATE OR REPLACE FUNCTION public.{name}(", 1)[1].split("$$;", 1)[0]
    assert "public.sp_save_event(" in body
    assert not re.search(
        r"\b(?:FROM|JOIN|UPDATE|INTO)\s+(?:public\.)?"
        r"(?:news|news_entities|news_clusters|news_impacts|catalysts|"
        r"catalyst_history|filings|collection_intelligence_events)\b",
        body, re.IGNORECASE,
    )
    assert not re.search(
        r"\b(?:UPDATE|INSERT INTO|DELETE FROM)\s+(?:public\.)?event_lake\b",
        body, re.IGNORECASE,
    )


def test_writer_revision_is_guarded_repeatable_and_matches_cutover_lookup():
    root = Path(__file__).parents[1] / "oneshot"
    revision = (root / "revise_event_lake_writers.sql").read_text()
    cutover = (root / "install_event_lake_cutover.sql").read_text()
    lookup = revision.split("lake_lookup text := $lake$", 1)[1].split("$lake$;", 1)[0]
    assert " ".join(lookup.split()) in " ".join(cutover.split())
    assert "AND association.entity_id = p_entity_id" in lookup
    assert "association.event_type = 'NEWS_ASSOCIATION'" in lookup
    assert revision.strip().startswith("BEGIN;")
    assert revision.strip().endswith("COMMIT;")
    assert "EXECUTE replace(definition, legacy_lookup, lake_lookup)" in revision
    assert "chr(13) || chr(10), chr(10)" in revision
    assert "ELSIF strpos(definition, lake_lookup) = 0" in revision
    assert "Unexpected news association lookup; no revision applied" in revision
    assert "does not delegate to sp_save_event" in revision
    assert "still references legacy projections" in revision
    assert "bypasses sp_save_event" in revision
    for name in (
        "sp_news_upsert_article", "sp_news_update_entity_scores", "sp_upsert_catalyst",
        "sp_upsert_filing", "sp_save_intelligence_event",
    ):
        assert f"'public.{name}(" in revision
    assert "DROP " not in revision


@pytest.mark.parametrize("line_ending", ["\n", "\r\n"])
def test_writer_revision_normalizes_lookup_literals_before_matching(line_ending):
    revision = (
        Path(__file__).parents[1] / "oneshot" / "revise_event_lake_writers.sql"
    ).read_text()
    match_position = revision.index("IF strpos(definition, legacy_lookup) > 0 THEN")
    for variable, delimiter in (("legacy_lookup", "legacy"), ("lake_lookup", "lake")):
        normalization = f"{variable} := replace({variable}, chr(13) || chr(10), chr(10));"
        assert revision.index(normalization) < match_position
        lookup = revision.split(f"{variable} text := ${delimiter}$", 1)[1].split(
            f"${delimiter}$;", 1
        )[0]
        windows_lookup = lookup.replace("\n", line_ending)
        assert windows_lookup.replace("\r\n", "\n") == lookup


def test_cutover_consolidation_preserves_immutable_entity_references():
    sql = (Path(__file__).parents[1] / "oneshot" / "install_event_lake_cutover.sql").read_text()
    body = sql.split("DO $consolidation$", 1)[1].split("$consolidation$;", 1)[0]
    assert "public.sp_consolidate_entity(uuid,uuid,boolean)" in body
    assert "definition := replace(definition, legacy_block, '')" in body
    assert "Missing entity consolidation guard location" in body
    assert "SELECT FROM public.event_lake" in body
    assert "WHERE entity_id = p_source_entity_id" in body
    assert "an entity redirect policy is required" in body
    assert "Entity consolidation still writes legacy archives" in body
    assert "UPDATE public.event_lake" not in body
    assert "DELETE FROM public.event_lake" not in body


def test_cutover_refuses_remaining_archive_writers_and_view_dependencies():
    sql = (Path(__file__).parents[1] / "oneshot" / "install_event_lake_cutover.sql").read_text()
    body = sql.split("DO $verification$", 1)[1].split("$verification$;", 1)[0]
    assert "legacy_block := regexp_substr(definition, block_pattern);" in sql
    assert "Legacy archive writers remain" in body
    assert "Views still depend on physical legacy archives" in body
    assert "dependency.refclassid = 'pg_class'::regclass" in body
    assert "dependency.classid = 'pg_rewrite'::regclass" in body


def test_archive_removal_requires_cutover_and_audit_without_cascade():
    sql = (Path(__file__).parents[1] / "oneshot" / "remove_event_legacy_tables.sql").read_text()
    assert sql.strip().startswith("BEGIN;")
    assert sql.strip().endswith("COMMIT;")
    assert sql.index("SET LOCAL TIME ZONE 'UTC';") < sql.index("DO $preflight$")
    assert "CASCADE" not in sql
    assert "snapshot.row_data = to_jsonb(original)" in sql
    assert "JOIN public.event_lake lake ON lake.event_id = mapping.event_id" in sql
    assert "relkind = 'v'" in sql
    assert "IN ACCESS EXCLUSIVE MODE" in sql
    assert "DROP TABLE event_legacy.news" in sql
    assert "DROP SCHEMA event_lake_backfill" not in sql
    assert "DROP TABLE public." not in sql


def test_reader_cutover_inlines_live_projections_and_removes_views_atomically():
    sql = (
        Path(__file__).parents[1] / "oneshot" / "revise_event_lake_readers.sql"
    ).read_text()
    assert sql.strip().startswith("BEGIN;")
    assert sql.strip().endswith("COMMIT;")
    assert "SET LOCAL search_path = pg_catalog;" in sql
    assert "pg_get_viewdef(view_oid, true)" in sql
    assert "pg_get_functiondef(routine.oid)" in sql
    assert "AS event_lake_latest" in sql
    assert "reference[2]" in sql and "reference[4]" in sql
    assert sql.index("EXECUTE definition;") < sql.index("DROP VIEW public.%I RESTRICT")
    assert "Stored routines still reference legacy event relations" in sql
    assert "CASCADE" not in sql
    assert "DROP TABLE" not in sql
    assert "DROP VIEW public.event_lake_latest" not in sql
    for name in (
        "news", "news_entities", "news_clusters", "news_impacts",
        "catalysts", "catalyst_history", "filings", "collection_intelligence_events",
    ):
        assert f"'{name}'" in sql


def test_reader_qualification_repair_is_guarded_and_preserves_latest_semantics():
    sql = (
        Path(__file__).parents[1] / "oneshot" / "repair_event_lake_reader_qualification.sql"
    ).read_text()
    assert sql.strip().startswith("BEGIN;")
    assert sql.strip().endswith("COMMIT;")
    assert "SET LOCAL search_path = pg_catalog;" in sql
    assert "pg_get_viewdef('public.event_lake_latest'::regclass, true)" in sql
    assert "projection_columns IS DISTINCT FROM expected_columns" in sql
    assert "Unexpected latest-event projection" in sql
    assert "News reader does not contain the expected inlined projection" in sql
    assert "to_regprocedure('public.sp_news_list_pending_entity_scores(integer)')" in sql
    assert "Missing enrichment reader" in sql
    assert "Enrichment reader does not contain the expected inlined projection" in sql
    assert sql.count("WHERE procedure.oid = enrichment_reader") == 2
    assert "pg_get_functiondef(routine.oid), original_projection, qualified_projection" in sql
    assert "strpos(procedure.prosrc, original_projection) > 0" in sql
    assert "strpos(procedure.prosrc, qualified_projection) > 0" in sql
    assert "format('lake.%I', column_name)" in sql
    assert "ORDER BY position" in sql
    assert "DISTINCT ON (lake.event_type, lake.stage, lake.source, lake.event_key, lake.entity_id)" in sql
    assert "FROM public.event_lake AS lake" in sql
    assert "ORDER BY lake.event_type, lake.stage, lake.source, lake.event_key, lake.entity_id, lake.revision DESC" in sql
    assert sql.index("News reader does not contain") < sql.index("EXECUTE definition;")
    assert sql.index("Enrichment reader does not contain") < sql.index("EXECUTE definition;")
    assert sql.index("EXECUTE definition;") < sql.index("Reader qualification verification failed")
    assert sql.index("EXECUTE definition;") < sql.index("Enrichment reader qualification verification failed")
    assert "zero means already repaired" in sql
    assert "variable_conflict" not in sql
    assert "DROP " not in sql
    assert "UPDATE public.event_lake" not in sql
    assert "CREATE VIEW" not in sql


def test_event_api_readers_use_stored_routines_not_legacy_relations():
    root = Path(__file__).parents[1]
    for relative_path, routine in (
        ("routers/news.py", "sp_news_get_user_news"),
        ("routers/catalysts.py", "sp_get_user_catalysts"),
        ("routers/entities.py", "sp_get_user_entity_overview"),
        ("services/news_enrichment.py", "sp_news_list_pending_entity_scores"),
        ("jobs/portfolio_intelligence/repositories/catalysts.py", "sp_get_recent_intelligence_catalysts"),
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


def test_lake_cleanup_defaults_to_preview_and_validates_runtime_threshold():
    sql = (
        Path(__file__).resolve().parents[1] / "oneshot" / "install_event_lake_cleanup.sql"
    ).read_text()
    assert "p_threshold_days integer DEFAULT 30" in sql
    assert "p_dry_run boolean DEFAULT true" in sql
    assert "p_threshold_days IS NULL OR p_threshold_days <= 0" in sql
    assert "IF p_dry_run IS NULL THEN" in sql
    assert "make_interval(days => p_threshold_days)" in sql
    assert "IF NOT p_dry_run THEN" in sql
    assert sql.index("IF NOT p_dry_run THEN") < sql.index("DELETE FROM public.event_lake")
    assert "GET DIAGNOSTICS deleted_rows = ROW_COUNT" in sql
    assert sql.startswith("BEGIN;")
    assert sql.rstrip().endswith("COMMIT;")


def test_lake_cleanup_protects_active_chains_future_events_and_incoming_references():
    sql = (
        Path(__file__).resolve().parents[1] / "oneshot" / "install_event_lake_cleanup.sql"
    ).read_text()
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
    sql = (
        Path(__file__).resolve().parents[1] / "oneshot" / "install_event_lake_cleanup.sql"
    ).read_text()
    assert "pg_get_functiondef('public.event_lake_timestamps()'::regprocedure)" in sql
    assert "Unexpected event lake deletion guard" in sql
    assert "current_setting('mip.event_lake_cleanup', true) IS DISTINCT FROM 'on'" in sql
    assert "WHERE candidate.event_id = OLD.event_id" in sql
    assert "RETURN OLD;" in sql
    assert "DISABLE TRIGGER" not in sql
    assert "SECURITY DEFINER" not in sql
    body = sql.split("AS $cleanup$", 1)[1].split("$cleanup$;", 1)[0]
    assert "COMMIT" not in body.replace("ON COMMIT DROP", "")
    assert "coalesce(previous_cleanup_setting, '')" in body
    assert "DROP TABLE pg_temp.event_lake_cleanup_candidates" in body


def test_lake_indexes_cover_incoming_foreign_keys_and_latest_ordering():
    root = Path(__file__).resolve().parents[1]
    sql = (root / "oneshot" / "install_event_lake_indexes.sql").read_text()
    view = (root / "oneshot" / "install_event_lake_cutover.sql").read_text()
    assert "ON public.event_lake USING btree (supersedes_event_id)" in sql
    assert "ON event_lake_backfill.event_map USING btree (event_id)" in sql
    ordering = "event_type, stage, source, event_key, entity_id, revision DESC"
    assert f"({ordering});" in sql
    assert f"ORDER BY {ordering};" in view
    assert "INCLUDE" not in sql
    assert "DROP INDEX" not in sql
    assert "ALTER TABLE" not in sql


def test_lake_index_installation_is_concurrent_and_validates_existing_definitions():
    sql = (
        Path(__file__).resolve().parents[1] / "oneshot" / "install_event_lake_indexes.sql"
    ).read_text()
    assert sql.count("CREATE INDEX CONCURRENTLY IF NOT EXISTS") == 3
    assert not re.search(r"^\s*(?:BEGIN|COMMIT);", sql, re.MULTILINE)
    assert "to_regclass('event_lake_backfill.event_map')" in sql
    assert "index_row.indisvalid, index_row.indisready" in sql
    assert "actual.columns <> expected.columns" in sql
    assert "actual.indrelid <> to_regclass(expected.table_name)" in sql
    assert "expected.descending_revision AND option.position = 6 THEN 3" in sql
    assert "index_row.indpred IS NULL AND index_row.indexprs IS NULL" in sql
    assert "Invalid or incompatible event lake index" in sql
    assert sql.rstrip().endswith("RESET lock_timeout;")
