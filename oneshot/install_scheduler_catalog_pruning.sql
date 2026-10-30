CREATE OR REPLACE FUNCTION public.sp_scheduler_prune_undefined_jobs(
    p_defined_job_keys text[]
)
RETURNS TABLE(job_id uuid, job_key varchar)
LANGUAGE plpgsql
AS $function$
DECLARE
    v_removed_ids uuid[];
    v_job_id uuid;
BEGIN
    IF p_defined_job_keys IS NULL
       OR cardinality(p_defined_job_keys) = 0
       OR EXISTS (
           SELECT FROM unnest(p_defined_job_keys) AS defined(job_key)
           WHERE NULLIF(trim(defined.job_key), '') IS NULL
       ) THEN
        RAISE EXCEPTION 'A nonempty validated job catalog is required';
    END IF;

    LOCK TABLE public.scheduler_jobs,
        public.scheduler_manual_run_requests,
        public.scheduler_job_runs IN SHARE ROW EXCLUSIVE MODE;

    IF EXISTS (
        SELECT FROM unnest(p_defined_job_keys) AS defined(job_key)
        WHERE NOT EXISTS (
            SELECT FROM public.scheduler_jobs AS jobs
            WHERE jobs.job_key = defined.job_key
        )
    ) THEN
        RAISE EXCEPTION 'All defined jobs must be registered before pruning';
    END IF;

    SELECT array_agg(jobs.job_id ORDER BY jobs.job_key)
    INTO v_removed_ids
    FROM public.scheduler_jobs AS jobs
    WHERE NOT (jobs.job_key = ANY(p_defined_job_keys));

    IF v_removed_ids IS NULL THEN
        RETURN;
    END IF;

    FOREACH v_job_id IN ARRAY v_removed_ids LOOP
        IF NOT public.sp_scheduler_try_job_lock(v_job_id) THEN
            RAISE EXCEPTION 'Cannot prune busy scheduler job %', v_job_id;
        END IF;
    END LOOP;

    IF EXISTS (
        SELECT FROM public.scheduler_job_runs AS runs
        WHERE runs.job_id = ANY(v_removed_ids) AND runs.completed_at IS NULL
    ) OR EXISTS (
        SELECT FROM public.scheduler_manual_run_requests AS requests
        WHERE requests.job_id = ANY(v_removed_ids)
          AND requests.claimed_at IS NOT NULL
          AND requests.completed_at IS NULL
    ) THEN
        RAISE EXCEPTION 'Cannot prune jobs with unfinished runs or claimed requests';
    END IF;

    UPDATE public.market_screener_runs AS screener
    SET job_run_id = NULL
    FROM public.scheduler_job_runs AS runs
    WHERE screener.job_run_id = runs.job_run_id
      AND runs.job_id = ANY(v_removed_ids);

    DELETE FROM public.scheduler_manual_run_requests AS requests
    WHERE requests.job_id = ANY(v_removed_ids);

    DELETE FROM public.scheduler_job_runs AS runs
    WHERE runs.job_id = ANY(v_removed_ids);

    RETURN QUERY
    DELETE FROM public.scheduler_jobs AS jobs
    WHERE jobs.job_id = ANY(v_removed_ids)
    RETURNING jobs.job_id, jobs.job_key;
END;
$function$;