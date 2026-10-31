BEGIN;

SET LOCAL TIME ZONE 'UTC';
SET LOCAL lock_timeout = '10s';

DO $preflight$
DECLARE
    archive_name text;
    missing_rows boolean;
BEGIN
    IF to_regclass('public.event_lake_latest') IS NULL
       OR to_regclass('event_lake_backfill.source_rows') IS NULL
       OR to_regclass('event_lake_backfill.event_map') IS NULL THEN
        RAISE EXCEPTION 'An installed cutover and retained backfill audit are required';
    END IF;
    FOREACH archive_name IN ARRAY ARRAY[
        'news', 'news_entities', 'news_clusters', 'news_impacts',
        'catalysts', 'catalyst_history', 'filings', 'collection_intelligence_events'
    ] LOOP
        IF NOT EXISTS (
            SELECT FROM pg_class
            WHERE oid = to_regclass(format('public.%I', archive_name))
              AND relkind = 'v'
        ) THEN
            RAISE EXCEPTION 'Expected lake-backed compatibility view: %', archive_name;
        END IF;
        EXECUTE format('LOCK TABLE event_legacy.%I IN ACCESS EXCLUSIVE MODE', archive_name);
        EXECUTE format(
            'SELECT EXISTS (
                SELECT FROM event_legacy.%I original
                WHERE NOT EXISTS (
                    SELECT FROM event_lake_backfill.source_rows snapshot
                    WHERE snapshot.table_name = $1
                      AND snapshot.row_data = to_jsonb(original)
                      AND EXISTS (
                          SELECT FROM event_lake_backfill.event_map mapping
                          JOIN public.event_lake lake ON lake.event_id = mapping.event_id
                          WHERE mapping.table_name = snapshot.table_name
                            AND mapping.legacy_key = snapshot.legacy_key
                      )
                )
            )', archive_name
        ) INTO missing_rows USING archive_name;
        IF missing_rows THEN
            RAISE EXCEPTION 'Legacy archive changed or audit mapping missing: %', archive_name;
        END IF;
    END LOOP;
END;
$preflight$;

DROP TABLE event_legacy.news, event_legacy.news_entities,
    event_legacy.news_clusters, event_legacy.news_impacts,
    event_legacy.catalysts, event_legacy.catalyst_history,
    event_legacy.filings, event_legacy.collection_intelligence_events;

DROP SCHEMA event_legacy;

COMMIT;