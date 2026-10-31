BEGIN;

SET LOCAL lock_timeout = '10s';
SET LOCAL search_path = pg_catalog;

DO $repair$
DECLARE
    original_projection text;
    qualified_projection text;
    projection_match text[];
    projection_columns text[];
    expected_columns text[];
    qualified_columns text;
    routine record;
    definition text;
    news_reader oid;
    enrichment_reader oid;
    revised integer := 0;
BEGIN
    news_reader := to_regprocedure(
        'public.sp_news_get_user_news(uuid,character varying,timestamp with time zone,timestamp with time zone,character varying,numeric,numeric,numeric,character varying,integer)'
    );
    IF news_reader IS NULL THEN
        RAISE EXCEPTION 'Missing news reader; review the installed schema before repair';
    END IF;
    enrichment_reader := to_regprocedure('public.sp_news_list_pending_entity_scores(integer)');
    IF enrichment_reader IS NULL THEN
        RAISE EXCEPTION 'Missing enrichment reader; review the installed schema before repair';
    END IF;

    original_projection := rtrim(
        pg_get_viewdef('public.event_lake_latest'::regclass, true), E';\n\r '
    );
    projection_match := regexp_match(
        original_projection,
        '^\s*SELECT\s+DISTINCT\s+ON\s*\(event_type,\s*stage,\s*source,\s*event_key,\s*entity_id\)\s+([a-z_0-9,\s]+)\s+FROM\s+public\.event_lake\s+ORDER\s+BY\s+event_type,\s*stage,\s*source,\s*event_key,\s*entity_id,\s*revision\s+DESC\s*$',
        'i'
    );
    IF projection_match IS NULL THEN
        RAISE EXCEPTION 'Unexpected latest-event projection; no automatic repair is safe';
    END IF;

    projection_columns := regexp_split_to_array(btrim(projection_match[1], E' \n\r\t'), '\s*,\s*');
    SELECT array_agg(attribute.attname::text ORDER BY attribute.attnum)
    INTO expected_columns
    FROM pg_attribute attribute
    WHERE attribute.attrelid = 'public.event_lake_latest'::regclass
      AND attribute.attnum > 0 AND NOT attribute.attisdropped;
    IF projection_columns IS DISTINCT FROM expected_columns THEN
        RAISE EXCEPTION 'Unexpected latest-event column list; no changes applied';
    END IF;

    SELECT string_agg(format('lake.%I', column_name), ', ' ORDER BY position)
    INTO qualified_columns
    FROM unnest(projection_columns) WITH ORDINALITY AS columns(column_name, position);
    qualified_projection :=
        'SELECT DISTINCT ON (lake.event_type, lake.stage, lake.source, lake.event_key, lake.entity_id) '
        || qualified_columns
        || ' FROM public.event_lake AS lake'
        || ' ORDER BY lake.event_type, lake.stage, lake.source, lake.event_key, lake.entity_id, lake.revision DESC';

    IF NOT EXISTS (
        SELECT 1 FROM pg_proc procedure
        WHERE procedure.oid = news_reader
          AND (strpos(procedure.prosrc, original_projection) > 0
               OR strpos(procedure.prosrc, qualified_projection) > 0)
    ) THEN
        RAISE EXCEPTION 'News reader does not contain the expected inlined projection';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc procedure
        WHERE procedure.oid = enrichment_reader
          AND (strpos(procedure.prosrc, original_projection) > 0
               OR strpos(procedure.prosrc, qualified_projection) > 0)
    ) THEN
        RAISE EXCEPTION 'Enrichment reader does not contain the expected inlined projection';
    END IF;

    FOR routine IN
        SELECT procedure.oid, procedure.proname
        FROM pg_proc procedure
        JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace
        WHERE namespace.nspname = 'public'
          AND procedure.prokind = 'f'
          AND strpos(procedure.prosrc, original_projection) > 0
        ORDER BY procedure.oid
    LOOP
        definition := replace(
            pg_get_functiondef(routine.oid), original_projection, qualified_projection
        );
        EXECUTE definition;
        revised := revised + 1;
        RAISE NOTICE 'Qualified latest-event projections in %', routine.oid::regprocedure;
    END LOOP;

    IF EXISTS (
        SELECT 1
        FROM pg_proc procedure
        JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace
        WHERE namespace.nspname = 'public'
          AND procedure.prokind = 'f'
          AND strpos(procedure.prosrc, original_projection) > 0
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_proc procedure
        WHERE procedure.oid = news_reader
          AND strpos(procedure.prosrc, qualified_projection) > 0
    ) THEN
        RAISE EXCEPTION 'Reader qualification verification failed';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc procedure
        WHERE procedure.oid = enrichment_reader
          AND strpos(procedure.prosrc, qualified_projection) > 0
    ) THEN
        RAISE EXCEPTION 'Enrichment reader qualification verification failed';
    END IF;
    RAISE NOTICE 'Repaired % readers; zero means already repaired', revised;
END;
$repair$;

COMMIT;