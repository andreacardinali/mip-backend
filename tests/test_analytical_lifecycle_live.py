import os

import pytest
from sqlalchemy import text

from market_analysis.lifecycle_repository import SignalLifecycleRepository

pytestmark = pytest.mark.skipif(
    os.environ.get("MIP_TEST_LIVE_DB") != "1",
    reason="Set MIP_TEST_LIVE_DB=1 to validate the configured database after cutover",
)


@pytest.fixture
def database():
    from db import engine

    with engine.connect().execution_options(
        isolation_level="REPEATABLE READ"
    ) as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        try:
            yield connection
        finally:
            connection.rollback()


def test_lifecycle_storage_and_routines_use_installed_lake(database):
    relations = database.execute(
        text(
            "SELECT name, to_regclass('public.' || name) IS NOT NULL AS present "
            "FROM unnest(ARRAY['analytical_lake', 'market_analysis_lifecycle_events', "
            "'market_analysis_signal_events', 'signals', 'signal_history', "
            "'signal_factors']) AS names(name)"
        )
    ).all()
    assert dict(relations) == {
        "analytical_lake": True,
        "market_analysis_lifecycle_events": True,
        "market_analysis_signal_events": False,
        "signals": False,
        "signal_history": False,
        "signal_factors": False,
    }
    references = (
        database.execute(
            text(
                "SELECT routine.oid::regprocedure::text FROM pg_proc routine "
                "JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace "
                "WHERE namespace.nspname = 'public' "
                "AND routine.prosrc ~ "
                "'\\m(market_analysis_signal_events|signals|signal_history|signal_factors)\\M'"
            )
        )
        .scalars()
        .all()
    )
    assert references == []
    definition = database.execute(
        text(
            "SELECT pg_get_viewdef('public.market_analysis_lifecycle_events'::regclass)"
        )
    ).scalar_one()
    assert "analytical_lake" in definition
    assert "lifecycle_event" in definition


def test_consolidation_guards_and_removed_table_references(database):
    definitions = dict(
        database.execute(
            text(
                "SELECT proname, prosrc FROM pg_proc WHERE oid IN ("
                "to_regprocedure('public.sp_consolidate_entity(uuid,uuid,boolean)'), "
                "to_regprocedure('public.sp_consolidate_instrument(uuid,uuid,boolean,boolean)'))"
            )
        ).all()
    )
    entity = definitions["sp_consolidate_entity"]
    assert "SELECT FROM public.event_lake" in entity
    assert "an entity redirect policy is required" in entity
    assert entity.index("SELECT FROM public.analytical_lake") < entity.index(
        "IF NOT p_force"
    )
    assert "sp_consolidate_entity(" in definitions["sp_consolidate_instrument"]
    dangling_references = database.execute(
        text(
            "SELECT names.name, routine.oid::regprocedure::text "
            "FROM unnest(ARRAY['entity_metrics', 'insider_transactions', "
            "'portfolio_impacts', 'entity_impact_history', 'cache_portfolio_exposure', "
            "'cache_portfolio_sector_exposure', 'cache_portfolio_theme_exposure', "
            "'entity_scores', 'scoring_components', 'sector_sentiment', "
            "'sync_jobs', 'sync_log', 'sync_mapping', 'system_jobs', "
            "'entity_relationships', 'entity_sectors', 'entity_tags', "
            "'etf_constituents', 'instrument_aliases', 'instrument_entity_exposure', "
            "'instrument_relations', 'notification_hashes', "
            "'portfolio_holdings_snapshots', 'tags']) AS names(name) "
            r"JOIN pg_proc routine ON routine.prosrc ~* ('\m' || names.name || '\M') "
            "JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace "
            "WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema') "
            "AND to_regclass('public.' || names.name) IS NULL"
        )
    ).all()
    assert dangling_references == []


def test_performance_preserves_asof_prices_and_direction_cycles(database):
    definitions = dict(
        database.execute(
            text(
                "SELECT proname, pg_get_functiondef(oid) FROM pg_proc "
                "WHERE pronamespace = 'public'::regnamespace AND proname IN "
                "('trg_market_analysis_archive_lifecycle', "
                "'sp_market_analysis_performance_price', "
                "'sp_market_analysis_list_signal_performance')"
            )
        ).all()
    )
    capture = definitions["trg_market_analysis_archive_lifecycle"]
    price = definitions["sp_market_analysis_performance_price"]
    history = definitions["sp_market_analysis_list_signal_performance"]
    assert "sp_save_analytical_event" in capture
    assert "NEW.instrument_id, NEW.observed_at, NEW.payload, true" in capture
    assert "jsonb_build_object('historical_backfill', false)" in capture
    assert "ts <= p_observed_at" in price
    assert "observed_at <= p_observed_at" in price
    assert "public.market_analysis_lifecycle_events" in history
    assert (
        "ORDER BY next_event.performance_recorded_at, next_event.signal_event_id"
        in history
    )
    assert "'CREATED', 'DIRECTION_CHANGED', 'CLOSED', 'EXPIRED'" in history
    assert "new_direction IN ('LONG', 'SHORT')" in history
    assert "CASE WHEN new_direction = 'SHORT' THEN -1 ELSE 1 END" in history
    assert "(valuation->>'price_at')::timestamptz >= observed_at" in history
    assert "exit_at >= observed_at" in history
    assert "OFFSET GREATEST(p_offset, 0)" in history
    assert (
        database.execute(
            text(
                "SELECT count(*) FROM pg_trigger WHERE "
                "tgrelid = 'public.market_analysis_lifecycle_events'::regclass "
                "AND tgfoid = 'public.trg_market_analysis_archive_lifecycle()'::regprocedure "
                "AND NOT tgisinternal AND tgenabled = 'O' AND tgtype = 69"
            )
        ).scalar_one()
        == 1
    )


def test_backfilled_history_preserves_original_records_and_links(database):
    invalid = database.execute(
        text(
            "SELECT count(*) FROM public.analytical_lake lake "
            "WHERE lake.provenance->>'operation' = 'legacy_lifecycle_backfill' AND ("
            "lake.payload->'lifecycle_event' IS DISTINCT FROM lake.raw_payload "
            "OR lake.payload->>'legacy_without_assessment' IS DISTINCT FROM 'true' "
            "OR NOT EXISTS (SELECT FROM public.market_analysis_signal_lake link "
            "WHERE link.event_id = lake.event_id "
            "AND link.signal_id = (lake.payload->>'signal_id')::uuid) "
            "OR NOT EXISTS (SELECT FROM public.market_analysis_lifecycle_events event "
            "WHERE to_jsonb(event) = lake.raw_payload))"
        )
    ).scalar_one()
    assert invalid == 0
    assert (
        database.execute(
            text(
                "SELECT count(*) FROM (SELECT signal_event_id "
                "FROM public.market_analysis_lifecycle_events GROUP BY signal_event_id "
                "HAVING count(*) <> 1) duplicates"
            )
        ).scalar_one()
        == 0
    )


def test_history_repository_reads_lake_without_changing_api(database):
    instruments = (
        database.execute(
            text(
                "SELECT DISTINCT instrument_id FROM public.market_analysis_lifecycle_events "
                "ORDER BY instrument_id LIMIT 5"
            )
        )
        .scalars()
        .all()
    )
    if not instruments:
        pytest.skip("No archived lifecycle records to exercise history reads")
    repository = SignalLifecycleRepository(database)
    for instrument_id in instruments:
        expected = (
            database.execute(
                text(
                    "SELECT to_jsonb(event) FROM public.market_analysis_lifecycle_events event "
                    "WHERE instrument_id = :instrument "
                    "ORDER BY observed_at DESC, created_at DESC LIMIT 100"
                ),
                {"instrument": instrument_id},
            )
            .scalars()
            .all()
        )
        actual = repository.list_events(str(instrument_id))
        assert actual == expected
        assert all(event["instrument_id"] == str(instrument_id) for event in actual)
        for status in ("OPEN", "CLOSED"):
            performance = repository.list_performance(
                instrument_id=str(instrument_id), status=status, limit=20
            )
            assert len(performance) <= 20
            assert all(
                event["instrument_id"] == str(instrument_id)
                and event["status"] == status
                for event in performance
            )
