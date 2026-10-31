BEGIN;

SET LOCAL lock_timeout = '10s';
SET LOCAL search_path = pg_catalog;

DO $migration$
DECLARE
    view_name text;
    view_oid oid;
    latest_projection text;
    projection text;
    projections jsonb := '{}'::jsonb;
    routine record;
    definition text;
    reference text[];
    view_names text[] := ARRAY[
        'news', 'news_entities', 'news_clusters', 'news_impacts',
        'catalysts', 'catalyst_history', 'filings',
        'collection_intelligence_events'
    ];
    relation_pattern text := '\m(?:FROM|JOIN)\s+(?:public\.)?(?:news|news_entities|news_clusters|news_impacts|catalysts|catalyst_history|filings|collection_intelligence_events)\M';
    revised integer := 0;
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_class relation
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relname = ANY(view_names)
    ) THEN
        RAISE NOTICE 'Legacy views already absent; no changes applied';
        RETURN;
    END IF;

    latest_projection := rtrim(pg_get_viewdef('public.event_lake_latest'::regclass, true), E';\n\r ');
    IF latest_projection NOT LIKE '%public.event_lake%' THEN
        RAISE EXCEPTION 'Latest-event projection does not read public.event_lake';
    END IF;

    FOREACH view_name IN ARRAY view_names LOOP
        SELECT relation.oid INTO view_oid
        FROM pg_class relation
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relname = view_name
          AND relation.relkind = 'v';
        IF view_oid IS NULL THEN
            RAISE EXCEPTION 'Missing compatibility view: %', view_name;
        END IF;

        projection := rtrim(pg_get_viewdef(view_oid, true), E';\n\r ');
        projection := regexp_replace(
            projection,
            '(\m(?:FROM|JOIN)\s+public\.event_lake_latest)(?=\s+(?:WHERE|ORDER|GROUP|LIMIT|UNION)\M|\s*$)',
            '\1 AS event_lake_latest',
            'gi'
        );
        projection := replace(projection, 'public.event_lake_latest', '(' || latest_projection || ')');
        IF projection ~* relation_pattern
           OR projection LIKE '%public.event_lake_latest%' THEN
            RAISE EXCEPTION 'Projection still depends on a compatibility view: %', view_name;
        END IF;
        projections := projections || jsonb_build_object(view_name, projection);
    END LOOP;

    FOR routine IN
        SELECT procedure.oid, procedure.proname, procedure.prosrc
        FROM pg_proc procedure
        JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace
        WHERE namespace.nspname = 'public'
          AND procedure.prokind = 'f'
          AND procedure.prosrc ~* relation_pattern
        ORDER BY procedure.oid
    LOOP
        definition := pg_get_functiondef(routine.oid);
        FOREACH view_name IN ARRAY view_names LOOP
            FOR reference IN
                SELECT DISTINCT matches
                FROM regexp_matches(
                    routine.prosrc,
                    '((\m(?:FROM|JOIN)\s+)((?:public\.)?' || view_name || '\M)(\s+(?:AS\s+)?[a-z_][a-z_0-9]*))',
                    'gi'
                ) AS matches
            LOOP
                IF reference[4] ~* '^\s+(?:WHERE|ON|ORDER|GROUP|LIMIT|UNION|INTERSECT|EXCEPT|JOIN|LEFT|RIGHT|INNER|FULL|CROSS)\M' THEN
                    RAISE EXCEPTION 'Unaliased relation requires manual review: % in %', view_name, routine.proname;
                END IF;
                definition := replace(
                    definition,
                    reference[1],
                    reference[2] || '(' || (projections->>view_name) || ')' || reference[4]
                );
            END LOOP;
        END LOOP;
        IF definition ~* relation_pattern THEN
            RAISE EXCEPTION 'Unconverted relation in %', routine.proname;
        END IF;
        EXECUTE definition;
        revised := revised + 1;
        RAISE NOTICE 'Revised %', routine.proname;
    END LOOP;

    IF EXISTS (
        SELECT 1
        FROM pg_proc procedure
        JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace
        WHERE namespace.nspname = 'public'
          AND procedure.prokind = 'f'
          AND procedure.prosrc ~* '\m(?:FROM|JOIN|UPDATE|INTO)\s+(?:public\.)?(?:news|news_entities|news_clusters|news_impacts|catalysts|catalyst_history|filings|collection_intelligence_events)\M'
    ) THEN
        RAISE EXCEPTION 'Stored routines still reference legacy event relations';
    END IF;

    FOREACH view_name IN ARRAY view_names LOOP
        EXECUTE format('DROP VIEW public.%I RESTRICT', view_name);
    END LOOP;
    RAISE NOTICE 'Revised % routines; removed eight compatibility views', revised;
END;
$migration$;

COMMIT;