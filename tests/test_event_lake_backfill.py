from pathlib import Path


def test_backfill_extracts_json_objects_before_removing_keys():
    sql = (Path(__file__).parents[1] / 'oneshot' / 'backfill_event_lake.sql').read_text()
    assert "'payload', (original.row_data->'payload') - ARRAY[" in sql
    assert "'evidence', (original.row_data->'evidence') - 'marketEvidence'" in sql
    assert "original.row_data->'payload' -" not in sql
    assert "original.row_data->'evidence' -" not in sql


def test_backfill_transaction_and_writer_contract():
    sql = (Path(__file__).parents[1] / 'oneshot' / 'backfill_event_lake.sql').read_text()
    assert sql.strip().startswith('BEGIN;')
    assert sql.strip().endswith('COMMIT;')
    assert 'ROLLBACK;' not in sql
    assert 'saved := public.sp_save_event(planned.envelope);' in sql
    assert 'INSERT INTO public.event_lake' not in sql
    assert 'UPDATE public.event_lake' not in sql
    assert 'DELETE FROM' not in sql
    assert 'DROP TABLE' not in sql


def test_backfill_audit_and_completeness_contract():
    sql = (Path(__file__).parents[1] / 'oneshot' / 'backfill_event_lake.sql').read_text()
    for requirement in (
        'CREATE TABLE event_lake_backfill.source_rows',
        'CREATE TABLE event_lake_backfill.event_map',
        "'legacy_row', original.row_data",
        "'legacy_table', original.table_name",
        "'parent_event_ids', parents",
        "'subjects', CASE WHEN plan.entity_id IS NULL",
        "WHERE table_name = 'collection_intelligence_events'",
        'Backfill contains unknown entities',
        'Backfill contains orphan legacy references',
        'Ambiguous existing event',
        'Incomplete legacy backfill',
        'Backfill contains inconsistent instrument subjects',
        'count(DISTINCT (lake.source, lake.event_key))',
        "'source', 'COLLECTION_RESEARCH'",
        'event_lake_backfill.stable_key(jsonb_build_array(',
        "WHEN 'QUARTERLY_EARNINGS' THEN 'EARNINGS'",
        "SELECT coalesce((lake.payload->>'catalyst_id')::uuid, root.event_id)",
        "jsonb_set(planned.envelope, '{payload,news_id}',",
        'mapping.entity_id IS NOT DISTINCT FROM plan.entity_id',
    ):
        assert requirement in sql