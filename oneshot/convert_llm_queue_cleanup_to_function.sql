-- Apply manually before deploying the SELECT-based cleanup command.
-- This transaction changes routines only; it never invokes queue cleanup.
BEGIN;

DO $conversion$
DECLARE
    v_expected record;
    v_routine record;
    v_body text;
    v_owner text;
    v_comment text;
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_default_acl
        WHERE defaclrole = current_user::regrole
          AND defaclobjtype = 'f'
          AND defaclnamespace IN (0, 'public'::regnamespace)
    ) THEN
        RAISE EXCEPTION
            'Custom default routine privileges require an explicit permission review';
    END IF;

    IF (
        SELECT count(*)
        FROM pg_proc routine
        JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
        WHERE namespace.nspname = 'public'
          AND routine.proname = 'sp_cleanup_llm_request_queue'
    ) <> 2 THEN
        RAISE EXCEPTION 'Expected exactly the two audited cleanup procedures';
    END IF;

    FOR v_expected IN
        SELECT * FROM (VALUES
            (
                'public.sp_cleanup_llm_request_queue(integer,integer,integer,boolean,interval,text,boolean,bigint,bigint,bigint,bigint,bigint)',
                '9f379c3f84a37f3b7419f27454a7c1dc'
            ),
            (
                'public.sp_cleanup_llm_request_queue(integer,integer,integer,boolean,interval,text,boolean,boolean,bigint,bigint,bigint,bigint,bigint,bigint)',
                '93da791f0ed465145344ff1fd841b38d'
            )
        ) AS expected(identity, source_md5)
        ORDER BY length(identity)
    LOOP
        SELECT routine.*, language.lanname
        INTO v_routine
        FROM pg_proc routine
        JOIN pg_language language ON language.oid = routine.prolang
        WHERE routine.oid = to_regprocedure(v_expected.identity);

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Missing audited procedure: %', v_expected.identity;
        END IF;
        IF v_routine.prokind <> 'p'
           OR v_routine.lanname <> 'plpgsql'
           OR md5(replace(v_routine.prosrc, E'\r\n', E'\n'))
                <> v_expected.source_md5
           OR v_routine.prosecdef
           OR v_routine.proconfig IS NOT NULL
           OR v_routine.proacl IS NOT NULL THEN
            RAISE EXCEPTION
                'Procedure changed since audit; review before conversion: %',
                v_expected.identity;
        END IF;
    END LOOP;

    v_owner := pg_get_userbyid(v_routine.proowner);
    v_comment := obj_description(v_routine.oid, 'pg_proc');
    v_body := replace(
        replace(v_routine.prosrc, E'\r\n', E'\n'),
        E'\nBEGIN\n',
        concat(
            E'\nBEGIN\n',
            E'    p_completed_count := 0;\n',
            E'    p_failed_count := 0;\n',
            E'    p_abandoned_count := 0;\n',
            E'    p_orphaned_pending_count := 0;\n',
            E'    p_expired_processing_count := 0;\n',
            E'    p_purged_count := 0;\n'
        )
    );

    EXECUTE format($definition$
        CREATE FUNCTION public.sp_cleanup_llm_request_queue(
            p_completed_retention_days integer DEFAULT 7,
            p_failed_retention_days integer DEFAULT 30,
            p_abandoned_retention_days integer DEFAULT 30,
            p_include_orphans boolean DEFAULT false,
            p_orphan_age interval DEFAULT interval '1 hour',
            p_queue_key text DEFAULT NULL,
            p_dry_run boolean DEFAULT true,
            p_purge_all boolean DEFAULT false,
            OUT p_completed_count bigint,
            OUT p_failed_count bigint,
            OUT p_abandoned_count bigint,
            OUT p_orphaned_pending_count bigint,
            OUT p_expired_processing_count bigint,
            OUT p_purged_count bigint
        ) RETURNS record
        LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE SECURITY INVOKER
        AS %L
    $definition$, v_body);

    EXECUTE format(
        'ALTER FUNCTION public.sp_cleanup_llm_request_queue(integer,integer,integer,boolean,interval,text,boolean,boolean) OWNER TO %I',
        v_owner
    );
    EXECUTE format(
        'COMMENT ON FUNCTION public.sp_cleanup_llm_request_queue(integer,integer,integer,boolean,interval,text,boolean,boolean) IS %L',
        v_comment
    );
    EXECUTE format(
        'REVOKE ALL ON FUNCTION public.sp_cleanup_llm_request_queue(integer,integer,integer,boolean,interval,text,boolean,boolean) FROM PUBLIC; '
        'GRANT EXECUTE ON FUNCTION public.sp_cleanup_llm_request_queue(integer,integer,integer,boolean,interval,text,boolean,boolean) TO PUBLIC, %I',
        v_owner
    );
END;
$conversion$;

DROP PROCEDURE public.sp_cleanup_llm_request_queue(
    integer, integer, integer, boolean, interval, text, boolean,
    bigint, bigint, bigint, bigint, bigint
) RESTRICT;

DROP PROCEDURE public.sp_cleanup_llm_request_queue(
    integer, integer, integer, boolean, interval, text, boolean, boolean,
    bigint, bigint, bigint, bigint, bigint, bigint
) RESTRICT;

DO $verify$
BEGIN
    IF (
        SELECT count(*)
        FROM pg_proc routine
        JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
        WHERE namespace.nspname = 'public'
          AND routine.proname = 'sp_cleanup_llm_request_queue'
    ) <> 1 OR NOT EXISTS (
        SELECT 1 FROM pg_proc
        WHERE oid = to_regprocedure(
            'public.sp_cleanup_llm_request_queue(integer,integer,integer,boolean,interval,text,boolean,boolean)'
        ) AND prokind = 'f' AND NOT proretset
    ) THEN
        RAISE EXCEPTION 'Cleanup conversion postcondition failed';
    END IF;
END;
$verify$;

COMMIT;