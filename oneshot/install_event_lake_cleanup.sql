BEGIN;

SET LOCAL lock_timeout = '10s';

DO $installation$
DECLARE
    definition text;
    deletion_guard text := 'RAISE EXCEPTION ''Event lake revisions cannot be deleted'';';
BEGIN
    IF to_regclass('public.event_lake') IS NULL
       OR to_regprocedure('public.event_lake_timestamps()') IS NULL THEN
        RAISE EXCEPTION 'An installed event lake is required';
    END IF;
    SELECT pg_get_functiondef('public.event_lake_timestamps()'::regprocedure)
    INTO definition;
    IF position('current_setting(''mip.event_lake_cleanup'', true)' IN definition) = 0 THEN
        IF position(deletion_guard IN definition) = 0 THEN
            RAISE EXCEPTION 'Unexpected event lake deletion guard; no cleanup installed';
        END IF;
        definition := replace(definition, deletion_guard, $guard$
        IF current_setting('mip.event_lake_cleanup', true) IS DISTINCT FROM 'on'
           OR to_regclass('pg_temp.event_lake_cleanup_candidates') IS NULL THEN
            RAISE EXCEPTION 'Event lake revisions cannot be deleted';
        END IF;
        IF NOT EXISTS (
            SELECT FROM pg_temp.event_lake_cleanup_candidates candidate
            WHERE candidate.event_id = OLD.event_id
        ) THEN
            RAISE EXCEPTION 'Event lake revision is outside the cleanup selection';
        END IF;
        RETURN OLD;
        $guard$);
        EXECUTE definition;
    END IF;
END;
$installation$;

CREATE OR REPLACE FUNCTION public.sp_cleanup_event_lake(
    p_threshold_days integer DEFAULT 30,
    p_dry_run boolean DEFAULT true
)
RETURNS TABLE (
    cutoff_at timestamptz,
    eligible_rows bigint,
    protected_rows bigint,
    deleted_rows bigint
)
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
SET lock_timeout = '10s'
AS $cleanup$
DECLARE
    reference record;
    join_condition text;
    previous_cleanup_setting text;
BEGIN
    IF p_threshold_days IS NULL OR p_threshold_days <= 0 THEN
        RAISE EXCEPTION 'Cleanup threshold must be a positive number of days'
            USING ERRCODE = '22023';
    END IF;
    IF p_dry_run IS NULL THEN
        RAISE EXCEPTION 'Cleanup dry-run flag cannot be null'
            USING ERRCODE = '22023';
    END IF;
    IF to_regclass('pg_temp.event_lake_cleanup_candidates') IS NOT NULL
       OR to_regclass('pg_temp.event_lake_cleanup_protected') IS NOT NULL THEN
        RAISE EXCEPTION 'An event lake cleanup selection already exists in this session';
    END IF;

    LOCK TABLE public.event_lake IN SHARE ROW EXCLUSIVE MODE;
    cutoff_at := statement_timestamp() - make_interval(days => p_threshold_days);
    deleted_rows := 0;

    CREATE TEMP TABLE event_lake_cleanup_protected (
        event_id uuid PRIMARY KEY
    ) ON COMMIT DROP;

    INSERT INTO pg_temp.event_lake_cleanup_protected (event_id)
    SELECT lake.event_id
    FROM public.event_lake lake
    JOIN (
        SELECT event_type, stage, source, event_key, entity_id
        FROM public.event_lake
        GROUP BY event_type, stage, source, event_key, entity_id
        HAVING max(last_seen_at) >= cutoff_at
            OR max(event_day) >= (statement_timestamp() AT TIME ZONE 'UTC')::date
            OR max(occurred_at) >= statement_timestamp()
    ) retained USING (event_type, stage, source, event_key)
    WHERE lake.entity_id IS NOT DISTINCT FROM retained.entity_id;

    FOR reference IN
        SELECT constraint_row.conrelid, constraint_row.conkey, constraint_row.confkey,
            namespace.nspname AS schema_name, relation.relname AS table_name
        FROM pg_constraint constraint_row
        JOIN pg_class relation ON relation.oid = constraint_row.conrelid
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE constraint_row.contype = 'f'
          AND constraint_row.confrelid = 'public.event_lake'::regclass
          AND constraint_row.conrelid <> constraint_row.confrelid
        ORDER BY constraint_row.conrelid, constraint_row.oid
    LOOP
        EXECUTE format('LOCK TABLE %I.%I IN SHARE MODE',
            reference.schema_name, reference.table_name);
        SELECT string_agg(format('referencing.%I = lake.%I',
            source_attribute.attname, target_attribute.attname), ' AND ' ORDER BY keys.position)
        INTO join_condition
        FROM unnest(reference.conkey, reference.confkey)
            WITH ORDINALITY AS keys(source_number, target_number, position)
        JOIN pg_attribute source_attribute
            ON source_attribute.attrelid = reference.conrelid
           AND source_attribute.attnum = keys.source_number
        JOIN pg_attribute target_attribute
            ON target_attribute.attrelid = 'public.event_lake'::regclass
           AND target_attribute.attnum = keys.target_number;
        EXECUTE format(
            'INSERT INTO pg_temp.event_lake_cleanup_protected (event_id)
             SELECT lake.event_id FROM public.event_lake lake
             JOIN %I.%I referencing ON %s ON CONFLICT DO NOTHING',
            reference.schema_name, reference.table_name, join_condition);
    END LOOP;

    WITH RECURSIVE edges AS (
        SELECT event_id, parent_id
        FROM public.event_lake
        CROSS JOIN LATERAL unnest(parent_event_ids) AS parents(parent_id)
        UNION
        SELECT event_id, supersedes_event_id
        FROM public.event_lake
        WHERE supersedes_event_id IS NOT NULL
        UNION
        SELECT supersedes_event_id, event_id
        FROM public.event_lake
        WHERE supersedes_event_id IS NOT NULL
    ), retained(event_id) AS (
        SELECT event_id FROM pg_temp.event_lake_cleanup_protected
        UNION
        SELECT edges.parent_id
        FROM retained
        JOIN edges ON edges.event_id = retained.event_id
    )
    INSERT INTO pg_temp.event_lake_cleanup_protected (event_id)
    SELECT retained.event_id
    FROM retained
    JOIN public.event_lake lake ON lake.event_id = retained.event_id
    ON CONFLICT DO NOTHING;

    CREATE TEMP TABLE event_lake_cleanup_candidates (
        event_id uuid PRIMARY KEY
    ) ON COMMIT DROP;
    INSERT INTO pg_temp.event_lake_cleanup_candidates (event_id)
    SELECT lake.event_id
    FROM public.event_lake lake
    WHERE NOT EXISTS (
        SELECT FROM pg_temp.event_lake_cleanup_protected retained
        WHERE retained.event_id = lake.event_id
    );

    SELECT count(*) INTO eligible_rows FROM pg_temp.event_lake_cleanup_candidates;
    SELECT count(*) INTO protected_rows FROM public.event_lake
    WHERE last_seen_at < cutoff_at AND event_id NOT IN (
        SELECT event_id FROM pg_temp.event_lake_cleanup_candidates
    );

    IF NOT p_dry_run THEN
        previous_cleanup_setting := current_setting('mip.event_lake_cleanup', true);
        PERFORM set_config('mip.event_lake_cleanup', 'on', true);
        DELETE FROM public.event_lake lake
        USING pg_temp.event_lake_cleanup_candidates candidate
        WHERE lake.event_id = candidate.event_id;
        GET DIAGNOSTICS deleted_rows = ROW_COUNT;
        PERFORM set_config('mip.event_lake_cleanup', coalesce(previous_cleanup_setting, ''), true);
    END IF;

    DROP TABLE pg_temp.event_lake_cleanup_candidates;
    DROP TABLE pg_temp.event_lake_cleanup_protected;
    RETURN NEXT;
END;
$cleanup$;

COMMIT;