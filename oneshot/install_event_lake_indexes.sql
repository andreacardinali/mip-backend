SET lock_timeout = '10s';

DO $preflight$
BEGIN
    IF to_regclass('public.event_lake') IS NULL
       OR to_regclass('public.event_lake_latest') IS NULL
       OR to_regclass('event_lake_backfill.event_map') IS NULL THEN
        RAISE EXCEPTION 'Event lake, latest view and retained backfill mapping are required';
    END IF;
END;
$preflight$;

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_event_lake_supersedes_event_id
    ON public.event_lake USING btree (supersedes_event_id);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_event_lake_backfill_event_id
    ON event_lake_backfill.event_map USING btree (event_id);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_event_lake_latest
    ON public.event_lake USING btree
    (event_type, stage, source, event_key, entity_id, revision DESC);

DO $validation$
DECLARE
    expected record;
    actual record;
BEGIN
    FOR expected IN
        SELECT * FROM (VALUES
            ('public.ix_event_lake_supersedes_event_id', 'public.event_lake',
             ARRAY['supersedes_event_id']::text[], false),
            ('event_lake_backfill.ix_event_lake_backfill_event_id',
             'event_lake_backfill.event_map', ARRAY['event_id']::text[], false),
            ('public.ix_event_lake_latest', 'public.event_lake',
             ARRAY['event_type', 'stage', 'source', 'event_key', 'entity_id', 'revision']::text[], true)
        ) AS definitions(index_name, table_name, columns, descending_revision)
    LOOP
        SELECT index_row.indisvalid, index_row.indisready,
               index_row.indrelid, access_method.amname,
               index_row.indpred IS NULL AND index_row.indexprs IS NULL AS plain,
               index_row.indnatts = index_row.indnkeyatts AS keys_only,
               index_row.indoption::smallint[] AS options,
               ARRAY(
                   SELECT attribute.attname::text
                   FROM unnest(index_row.indkey) WITH ORDINALITY AS key(attnum, position)
                   JOIN pg_catalog.pg_attribute attribute
                     ON attribute.attrelid = index_row.indrelid
                    AND attribute.attnum = key.attnum
                   ORDER BY key.position
               ) AS columns
        INTO actual
        FROM pg_catalog.pg_index index_row
        JOIN pg_catalog.pg_class index_relation ON index_relation.oid = index_row.indexrelid
        JOIN pg_catalog.pg_am access_method ON access_method.oid = index_relation.relam
        WHERE index_row.indexrelid = to_regclass(expected.index_name);

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Missing event lake index: %', expected.index_name;
        END IF;
        IF NOT actual.indisvalid OR NOT actual.indisready
           OR actual.indrelid <> to_regclass(expected.table_name)
           OR actual.amname <> 'btree' OR NOT actual.plain OR NOT actual.keys_only
           OR actual.columns <> expected.columns
           OR EXISTS (
               SELECT FROM unnest(actual.options) WITH ORDINALITY AS option(flags, position)
               WHERE option.flags <> CASE
                   WHEN expected.descending_revision AND option.position = 6 THEN 3
                   ELSE 0
               END
           ) THEN
            RAISE EXCEPTION 'Invalid or incompatible event lake index: %', expected.index_name
                USING HINT = 'Inspect the existing index before retrying; IF NOT EXISTS does not repair an interrupted build or a conflicting definition.';
        END IF;
    END LOOP;
END;
$validation$;

RESET lock_timeout;