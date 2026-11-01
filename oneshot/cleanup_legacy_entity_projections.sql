BEGIN;
SET LOCAL TIME ZONE 'UTC';
SET LOCAL lock_timeout = '10s';

DO $$
DECLARE
    v_tables text[] := ARRAY[
        'entity_metrics', 'insider_transactions', 'portfolio_impacts',
        'entity_impact_history', 'cache_portfolio_exposure'
    ];
    v_pattern text := '\m(entity_metrics|insider_transactions|portfolio_impacts|entity_impact_history|cache_portfolio_exposure)\M';
    v_routine regprocedure := 'public.sp_consolidate_entity(uuid,uuid,boolean)'::regprocedure;
    v_name text;
    v_relation regclass;
    v_has_rows boolean;
    v_source text;
    v_definition text;
    v_start integer;
    v_end integer;
    v_block record;
BEGIN
    FOREACH v_name IN ARRAY v_tables LOOP
        v_relation := to_regclass('public.' || v_name);
        IF v_relation IS NOT NULL THEN
            IF NOT EXISTS (
                SELECT FROM pg_class
                WHERE oid = v_relation AND relkind = 'r'
            ) THEN
                RAISE EXCEPTION 'Expected an ordinary table: public.%', v_name;
            END IF;
            EXECUTE format('LOCK TABLE public.%I IN ACCESS EXCLUSIVE MODE', v_name);
            EXECUTE format('SELECT EXISTS (SELECT FROM public.%I)', v_name)
                INTO v_has_rows;
            IF v_has_rows THEN
                RAISE EXCEPTION 'Legacy table public.% is not empty; cleanup aborted', v_name;
            END IF;
        END IF;
    END LOOP;

    IF EXISTS (
        SELECT FROM pg_proc routine
        JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
                WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema')
                    AND routine.prosrc ~* v_pattern
          AND routine.oid <> v_routine
    ) THEN
                RAISE EXCEPTION 'Another routine references the legacy tables';
    END IF;

    SELECT prosrc, pg_get_functiondef(oid)
    INTO STRICT v_source, v_definition
    FROM pg_proc WHERE oid = v_routine;

    IF v_source ~* v_pattern THEN
        IF md5(v_source) <> '5db35e6a909767f6f50906da80b5db4f' THEN
            RAISE EXCEPTION 'Entity consolidation definition changed; re-audit before cleanup';
        END IF;
        v_definition := replace(v_definition, E'\r\n', E'\n');
        FOR v_block IN
            SELECT * FROM (VALUES
                ('    INSERT INTO entity_metrics (', E'    /*\n     * Simple entity references.'),
                ('    UPDATE insider_transactions', E'    /*\n     * Entity relationships.')
            ) AS blocks(start_marker, end_marker)
        LOOP
            v_start := strpos(v_definition, v_block.start_marker);
            v_end := strpos(v_definition, v_block.end_marker);
            IF v_start = 0 OR v_end <= v_start THEN
                RAISE EXCEPTION 'Cannot locate legacy block beginning with %', v_block.start_marker;
            END IF;
            v_definition := substr(v_definition, 1, v_start - 1)
                || substr(v_definition, v_end);
        END LOOP;
        IF v_definition ~* v_pattern THEN
            RAISE EXCEPTION 'Legacy references remain after transforming entity consolidation';
        END IF;
        EXECUTE v_definition;
    END IF;

    SELECT prosrc INTO STRICT v_source FROM pg_proc WHERE oid = v_routine;
    IF v_source NOT LIKE '%SELECT FROM public.event_lake%'
       OR v_source NOT LIKE '%SELECT FROM public.analytical_lake%' THEN
        RAISE EXCEPTION 'Entity consolidation is missing immutable lake guards';
    END IF;
    IF EXISTS (
        SELECT FROM pg_proc routine
        JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
                WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema')
                    AND routine.prosrc ~* v_pattern
    ) THEN
                RAISE EXCEPTION 'A routine still references a legacy table';
    END IF;

    FOREACH v_name IN ARRAY v_tables LOOP
        EXECUTE format('DROP TABLE IF EXISTS public.%I RESTRICT', v_name);
    END LOOP;
END;
$$;

SELECT name, to_regclass('public.' || name) IS NULL AS removed
FROM unnest(ARRAY[
    'entity_metrics', 'insider_transactions', 'portfolio_impacts',
    'entity_impact_history', 'cache_portfolio_exposure'
]) AS tables(name);

COMMIT;