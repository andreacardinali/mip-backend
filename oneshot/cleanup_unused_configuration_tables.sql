BEGIN;
SET LOCAL TIME ZONE 'UTC';
SET LOCAL lock_timeout = '10s';

DO $$
DECLARE
    v_tables text[] := ARRAY[
        'api_sources', 'notification_rules', 'notification_templates',
        'system_settings', 'portfolio_snapshots'
    ];
    v_pattern text := '\m(api_sources|notification_rules|notification_templates|system_settings|portfolio_snapshots)\M';
    v_name text;
    v_relation regclass;
    v_has_rows boolean;
BEGIN
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
    ) THEN
        RAISE EXCEPTION 'A routine references a cleanup target; re-audit before cleanup';
    END IF;

    FOREACH v_name IN ARRAY v_tables LOOP
        EXECUTE format('DROP TABLE IF EXISTS public.%I RESTRICT', v_name);
    END LOOP;
END;
$$;

SELECT name, to_regclass('public.' || name) IS NULL AS removed
FROM unnest(ARRAY[
    'api_sources', 'notification_rules', 'notification_templates',
    'system_settings', 'portfolio_snapshots'
]) AS tables(name);

COMMIT;