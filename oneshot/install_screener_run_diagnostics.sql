BEGIN;
SET LOCAL lock_timeout = '10s';

DO $installation$
DECLARE
    definition text;
    anchor text := 'GET DIAGNOSTICS v_recovered = ROW_COUNT;';
    synchronization text := $sync$
    UPDATE public.market_screener_runs screener
    SET status = 'FAILED', completed_at = job_run.completed_at,
        updated_at = now(), error_message = COALESCE(job_run.error_message,
            'Analysis interrupted before completion')
    FROM public.scheduler_job_runs job_run
    WHERE screener.job_run_id = job_run.job_run_id
      AND job_run.job_id = p_job_id AND screener.status = 'RUNNING'
      AND job_run.status IN ('FAILED', 'SUCCESS', 'PARTIAL');
$sync$;
BEGIN
    definition := pg_get_functiondef('public.sp_scheduler_recover_job_runs(uuid)'::regprocedure);
    IF position('UPDATE public.market_screener_runs screener' IN definition) = 0 THEN
        IF position(anchor IN definition) = 0 THEN
            RAISE EXCEPTION 'Unexpected scheduler recovery definition';
        END IF;
        EXECUTE replace(definition, anchor, anchor || synchronization);
    END IF;
END;
$installation$;

CREATE OR REPLACE FUNCTION public.sp_market_screener_list_runs(p_limit integer DEFAULT 50)
RETURNS SETOF jsonb LANGUAGE sql STABLE AS $$
    SELECT jsonb_build_object(
        'screener_run_id', run.screener_run_id,
        'job_run_id', run.job_run_id,
        'job_key', job.job_key,
        'status', CASE WHEN run.status = 'RUNNING'
            AND job_run.status IN ('FAILED', 'SUCCESS', 'PARTIAL')
            THEN 'FAILED' ELSE run.status END,
        'discovery_snapshot_id', run.discovery_snapshot_id,
        'scan_run_id', run.scan_run_id,
        'started_at', run.started_at,
        'completed_at', CASE WHEN run.status = 'RUNNING'
            AND job_run.status IN ('FAILED', 'SUCCESS', 'PARTIAL')
            THEN job_run.completed_at ELSE run.completed_at END,
        'phase_results', run.phase_results,
        'error_message', CASE WHEN run.status = 'RUNNING'
            AND job_run.status IN ('FAILED', 'SUCCESS', 'PARTIAL')
            THEN COALESCE(job_run.error_message, 'Analysis interrupted before completion')
            ELSE run.error_message END,
        'ranking_snapshot_id', snapshot.ranking_snapshot_id,
        'opportunity_count', COALESCE(snapshot.opportunity_count, 0)
    )
    FROM public.market_screener_runs run
    LEFT JOIN public.scheduler_job_runs job_run ON job_run.job_run_id = run.job_run_id
    LEFT JOIN public.scheduler_jobs job ON job.job_id = job_run.job_id
    LEFT JOIN public.market_screener_ranking_snapshots snapshot
        ON snapshot.screener_run_id = run.screener_run_id
    ORDER BY run.started_at DESC
    LIMIT LEAST(GREATEST(COALESCE(p_limit, 50), 1), 500);
$$;

COMMIT;