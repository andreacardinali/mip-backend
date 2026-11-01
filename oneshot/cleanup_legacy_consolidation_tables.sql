BEGIN;
SET LOCAL TIME ZONE 'UTC';
SET LOCAL lock_timeout = '10s';
SET LOCAL search_path = pg_catalog, public;

DO $$
DECLARE
    v_tables text[] := ARRAY[
        'entity_relationships', 'entity_sectors', 'entity_tags',
        'etf_constituents', 'instrument_aliases', 'instrument_entity_exposure',
        'instrument_relations', 'notification_hashes',
        'portfolio_holdings_snapshots', 'tags'
    ];
    v_pattern text := '\m(entity_relationships|entity_sectors|entity_tags|etf_constituents|instrument_aliases|instrument_entity_exposure|instrument_relations|notification_hashes|portfolio_holdings_snapshots|tags)\M';
    v_entity regprocedure := to_regprocedure('public.sp_consolidate_entity(uuid,uuid,boolean)');
    v_instrument regprocedure := to_regprocedure('public.sp_consolidate_instrument(uuid,uuid,boolean,boolean)');
    v_name text;
    v_relation regclass;
    v_has_rows boolean;
    v_routine record;
    v_block record;
    v_source text;
    v_original text;
    v_definition text;
    v_start integer;
    v_end integer;
BEGIN
    IF v_entity IS NULL OR v_instrument IS NULL THEN
        RAISE EXCEPTION 'Expected both audited consolidation functions; cleanup aborted';
    END IF;

    FOREACH v_name IN ARRAY v_tables LOOP
        v_relation := to_regclass('public.' || v_name);
        IF v_relation IS NOT NULL THEN
            IF NOT EXISTS (
                SELECT FROM pg_class
                WHERE oid = v_relation AND relkind = 'r'
                  AND NOT relispartition
            ) OR EXISTS (
                SELECT FROM pg_inherits
                WHERE inhrelid = v_relation OR inhparent = v_relation
            ) OR EXISTS (
                SELECT FROM pg_depend
                WHERE classid = 'pg_class'::regclass AND objid = v_relation
                  AND refclassid = 'pg_extension'::regclass AND deptype = 'e'
            ) THEN
                RAISE EXCEPTION 'Expected an ordinary standalone table: public.%', v_name;
            END IF;
            EXECUTE format('LOCK TABLE public.%I IN ACCESS EXCLUSIVE MODE', v_name);
            EXECUTE format('SELECT EXISTS (SELECT FROM public.%I)', v_name)
                INTO v_has_rows;
            IF v_has_rows THEN
                RAISE EXCEPTION 'Table public.% is not empty; cleanup aborted', v_name;
            END IF;
        END IF;
    END LOOP;

    IF EXISTS (
        SELECT FROM pg_proc routine
        JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
        WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema')
          AND routine.prosrc ~* v_pattern
          AND routine.oid NOT IN (v_entity, v_instrument)
    ) THEN
        RAISE EXCEPTION 'Another routine references a cleanup target; re-audit before cleanup';
    END IF;

    FOR v_routine IN
        SELECT * FROM (VALUES
            (v_entity, 'b7ec6eaa6d6001893b1d44a000f6bbe3',
                'dcf23fcae917be8b5e1d79cf50797028'),
            (v_instrument, '83ec40410b340d5b8559cbbdc563b3c0',
                'b2f39d660b79ca2ad058a70000599507')
        ) AS routines(routine_oid, expected_hash, cleaned_hash)
    LOOP
        SELECT prosrc, pg_get_functiondef(oid)
        INTO STRICT v_original, v_definition
        FROM pg_proc
        WHERE oid = v_routine.routine_oid AND prokind = 'f'
          AND prorettype = 'jsonb'::regtype;

        IF md5(v_original) NOT IN (v_routine.expected_hash, v_routine.cleaned_hash) THEN
            RAISE EXCEPTION 'Definition of % changed; re-audit before cleanup',
                v_routine.routine_oid;
        END IF;
        IF v_original ~* v_pattern THEN
            v_source := replace(v_original, E'\r\n', E'\n');
            FOR v_block IN
                SELECT * FROM (VALUES
                    (v_entity, 1, '    INSERT INTO entity_sectors (',
                        E'    /*\n     * Simple entity references.'),
                    (v_entity, 2, '    UPDATE etf_constituents',
                        '    UPDATE notifications'),
                    (v_entity, 3, '    UPDATE notification_hashes',
                        E'    /*\n     * Prefer target metadata, fill only missing fields.'),
                    (v_instrument, 1, E'    /*\n     * Portfolio holding snapshots:',
                        E'    /*\n     * Price refresh queue has UNIQUE(instrument_id).'),
                    (v_instrument, 2, E'    /*\n     * Preserve aliases. The source symbol is useful when providers use',
                        E'    /*\n     * Record the operation before removing the source.')
                ) AS blocks(routine_oid, block_order, start_marker, end_marker)
                WHERE routine_oid = v_routine.routine_oid
                ORDER BY block_order
            LOOP
                v_start := strpos(v_source, v_block.start_marker);
                v_end := strpos(v_source, v_block.end_marker);
                IF v_start = 0 OR v_end <= v_start
                   OR strpos(substr(v_source, v_start + 1), v_block.start_marker) > 0
                   OR strpos(substr(v_source, v_end + 1), v_block.end_marker) > 0 THEN
                    RAISE EXCEPTION 'Cannot uniquely locate legacy block in % beginning with %',
                        v_routine.routine_oid, v_block.start_marker;
                END IF;
                v_source := substr(v_source, 1, v_start - 1)
                    || substr(v_source, v_end);
            END LOOP;
            IF v_source ~* v_pattern THEN
                RAISE EXCEPTION 'Legacy references remain after transforming %',
                    v_routine.routine_oid;
            END IF;
            IF md5(v_source) <> v_routine.cleaned_hash THEN
                RAISE EXCEPTION 'Unexpected transformed body for %; cleanup aborted',
                    v_routine.routine_oid;
            END IF;
            IF strpos(v_definition, v_original) = 0 THEN
                RAISE EXCEPTION 'Cannot locate original body in definition of %',
                    v_routine.routine_oid;
            END IF;
            EXECUTE replace(v_definition, v_original, v_source);
        END IF;
    END LOOP;

    SELECT prosrc INTO STRICT v_source FROM pg_proc WHERE oid = v_entity;
    IF v_source NOT LIKE '%SELECT FROM public.event_lake%'
       OR v_source NOT LIKE '%SELECT FROM public.analytical_lake%' THEN
        RAISE EXCEPTION 'Entity consolidation is missing immutable lake guards';
    END IF;
    SELECT prosrc INTO STRICT v_source FROM pg_proc WHERE oid = v_instrument;
    IF v_source NOT LIKE '%PERFORM sp_consolidate_entity(%'
       OR v_source NOT LIKE '%v_moved_snapshots integer := 0;%'
       OR v_source NOT LIKE '%''moved_snapshots'', v_moved_snapshots%' THEN
        RAISE EXCEPTION 'Instrument consolidation contract changed; cleanup aborted';
    END IF;
    IF EXISTS (
        SELECT FROM pg_proc routine
        JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
        WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema')
          AND routine.prosrc ~* v_pattern
    ) THEN
        RAISE EXCEPTION 'A routine still references a cleanup target';
    END IF;

    FOREACH v_name IN ARRAY v_tables LOOP
        EXECUTE format('DROP TABLE IF EXISTS public.%I RESTRICT', v_name);
    END LOOP;
END;
$$;

SELECT name, to_regclass('public.' || name) IS NULL AS removed
FROM unnest(ARRAY[
    'entity_relationships', 'entity_sectors', 'entity_tags',
    'etf_constituents', 'instrument_aliases', 'instrument_entity_exposure',
    'instrument_relations', 'notification_hashes',
    'portfolio_holdings_snapshots', 'tags'
]) AS tables(name);

COMMIT;