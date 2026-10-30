BEGIN;

ALTER TABLE public.scheduler_jobs ADD COLUMN IF NOT EXISTS description text;

CREATE OR REPLACE FUNCTION public.sp_register_scheduler_job(p_job_key character varying, p_job_name character varying, p_python_module character varying, p_trigger_type character varying, p_cron_expression character varying, p_interval_minutes integer, p_enabled_by_default boolean, p_description text) RETURNS TABLE(job_id uuid, job_key character varying, action character varying)
LANGUAGE plpgsql
AS $function$
DECLARE
    v_registration record;
BEGIN
    IF NULLIF(TRIM(p_description), '') IS NULL THEN
        RAISE EXCEPTION 'description cannot be empty';
    END IF;

    SELECT * INTO v_registration
    FROM public.sp_register_scheduler_job(
        p_job_key, p_job_name, p_python_module, p_trigger_type,
        p_cron_expression, p_interval_minutes, p_enabled_by_default
    );

    UPDATE public.scheduler_jobs AS sj
    SET description = p_description
    WHERE sj.job_id = v_registration.job_id
      AND sj.description IS DISTINCT FROM p_description;

    IF FOUND AND v_registration.action = 'UNCHANGED' THEN
        v_registration.action := 'UPDATED';
    END IF;

    RETURN QUERY
    SELECT v_registration.job_id, v_registration.job_key, v_registration.action;
END;
$function$;

DROP FUNCTION public.sp_list_scheduler_jobs();

CREATE FUNCTION public.sp_list_scheduler_jobs() RETURNS TABLE(job_id uuid, job_key character varying, job_name character varying, python_module character varying, trigger_type character varying, cron_expression character varying, interval_minutes integer, enabled boolean, last_run_at timestamp with time zone, next_run_at timestamp with time zone, last_status character varying, last_error text, updated_at timestamp with time zone, description text)
    LANGUAGE sql STABLE
    AS $$
    SELECT
        sj.job_id,
        sj.job_key,
        sj.job_name,
        sj.python_module,
        sj.trigger_type,
        sj.cron_expression,
        sj.interval_minutes,
        sj.enabled,
        latest_run.started_at AS last_run_at,
        CASE
            WHEN sj.enabled THEN sj.next_run_at
            ELSE NULL
        END AS next_run_at,
        latest_run.status AS last_status,
        latest_run.error_message AS last_error,
        sj.updated_at,
        sj.description
    FROM public.scheduler_jobs sj
    LEFT JOIN LATERAL (
        SELECT
            sjr.started_at,
            sjr.status,
            sjr.error_message
        FROM public.scheduler_job_runs sjr
        WHERE sjr.job_id = sj.job_id
        ORDER BY sjr.started_at DESC
        LIMIT 1
    ) latest_run ON TRUE
    ORDER BY sj.job_name;
$$;

COMMIT;