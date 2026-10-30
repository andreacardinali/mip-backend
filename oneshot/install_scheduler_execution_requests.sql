BEGIN;

ALTER TABLE public.scheduler_manual_run_requests
    ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS source varchar NOT NULL DEFAULT 'ADMIN_UI';

ALTER TABLE public.market_screener_runs
    ADD COLUMN IF NOT EXISTS job_run_id uuid REFERENCES public.scheduler_job_runs(job_run_id);

CREATE INDEX IF NOT EXISTS ix_market_screener_runs_job_run
    ON public.market_screener_runs(job_run_id);

CREATE OR REPLACE FUNCTION public.sp_scheduler_request_execution(
    p_job_key varchar, p_requested_by uuid, p_payload jsonb, p_source varchar
) RETURNS TABLE(request_id uuid, job_run_id uuid, status varchar)
LANGUAGE plpgsql AS $$
DECLARE
    v_job_id uuid;
BEGIN
    IF p_requested_by IS NULL OR jsonb_typeof(p_payload) IS DISTINCT FROM 'object'
       OR p_source IS NULL OR btrim(p_source) = '' THEN
        RAISE EXCEPTION 'Invalid execution request' USING ERRCODE = '22023';
    END IF;
    SELECT sj.job_id INTO v_job_id FROM public.scheduler_jobs sj
    WHERE sj.job_key = p_job_key;
    IF v_job_id IS NULL THEN
        RAISE EXCEPTION 'Scheduler job not found: %', p_job_key USING ERRCODE = 'P0002';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'mip.request.' || v_job_id::text || p_requested_by::text || p_payload::text, 0
    ));
    RETURN QUERY SELECT r.request_id, r.job_run_id, 'QUEUED'::varchar
        FROM public.scheduler_manual_run_requests r
        WHERE r.job_id = v_job_id AND r.requested_by = p_requested_by
          AND r.payload = p_payload AND r.status IN ('PENDING', 'PROCESSING')
        ORDER BY r.requested_at LIMIT 1;
    IF FOUND THEN RETURN; END IF;
    RETURN QUERY
        INSERT INTO public.scheduler_manual_run_requests(job_id, requested_by, payload, source)
        VALUES (v_job_id, p_requested_by, p_payload, p_source)
        RETURNING scheduler_manual_run_requests.request_id,
            scheduler_manual_run_requests.job_run_id, 'QUEUED'::varchar;
    PERFORM public.sp_record_system_audit(
        p_requested_by, 'SCHEDULER_JOB_RUN_REQUESTED', 'SCHEDULER_JOB', p_job_key,
        jsonb_build_object('source', p_source, 'payload', p_payload)
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_scheduler_claim_execution_requests(p_limit integer DEFAULT 5)
RETURNS TABLE(request_id uuid, job_run_id uuid, job_id uuid, job_key varchar,
    job_name varchar, python_module varchar, payload jsonb, requested_by uuid, source varchar)
LANGUAGE sql AS $$
    SELECT c.*, r.payload, r.requested_by, r.source
    FROM public.sp_scheduler_claim_job_runs(p_limit) c
    JOIN public.scheduler_manual_run_requests r ON r.request_id = c.request_id;
$$;

CREATE OR REPLACE FUNCTION public.sp_start_scheduler_job(
    p_job_run_id uuid, p_job_id uuid, p_started_at timestamptz
) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO public.scheduler_job_runs AS run(job_run_id, job_id, started_at, status)
    VALUES (p_job_run_id, p_job_id, p_started_at, 'RUNNING')
    ON CONFLICT (job_run_id) DO UPDATE SET
        started_at = EXCLUDED.started_at, status = 'RUNNING',
        completed_at = NULL, error_message = NULL, result = NULL
    WHERE run.job_id = p_job_id AND run.status = 'SUCCESS'
      AND run.result->>'reason' = 'analysis_already_running'
      AND EXISTS (SELECT 1 FROM public.scheduler_manual_run_requests request
          WHERE request.job_run_id = p_job_run_id AND request.status = 'PROCESSING');
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Execution already started: %', p_job_run_id;
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_market_screener_start_job_run(p_job_run_id uuid)
RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE v_run_id uuid;
BEGIN
    v_run_id := public.sp_market_screener_start_run();
    UPDATE public.market_screener_runs SET job_run_id = p_job_run_id
    WHERE screener_run_id = v_run_id;
    RETURN v_run_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_market_screener_fail_job_run(p_job_run_id uuid, p_error text)
RETURNS void LANGUAGE sql AS $$
    UPDATE public.market_screener_runs SET status = 'FAILED', error_message = p_error,
        completed_at = now(), updated_at = now()
    WHERE job_run_id = p_job_run_id AND status = 'RUNNING';
$$;

CREATE OR REPLACE FUNCTION public.sp_scheduler_execution_status(p_job_run_id uuid, p_user_id uuid)
RETURNS jsonb LANGUAGE sql STABLE AS $$
    SELECT jsonb_build_object(
        'request_id', request.request_id, 'job_run_id', request.job_run_id,
        'job_key', job.job_key, 'source', request.source,
        'status', CASE WHEN request.status = 'PENDING' THEN 'QUEUED'
            WHEN request.status = 'FAILED' THEN 'FAILED'
            WHEN run.status IS NOT NULL THEN run.status
            WHEN request.status = 'COMPLETED' THEN 'SUCCESS' ELSE 'QUEUED' END,
        'requested_at', request.requested_at, 'started_at', run.started_at,
        'completed_at', request.completed_at,
        'error_message', coalesce(request.error_message, run.error_message),
        'result', run.result, 'domain_run_id', screener.screener_run_id,
        'phase_results', coalesce(screener.phase_results, '{}'::jsonb)
    )
    FROM public.scheduler_manual_run_requests request
    JOIN public.scheduler_jobs job ON job.job_id = request.job_id
    LEFT JOIN public.scheduler_job_runs run ON run.job_run_id = request.job_run_id
    LEFT JOIN public.market_screener_runs screener ON screener.job_run_id = request.job_run_id
    WHERE request.job_run_id = p_job_run_id AND request.requested_by = p_user_id
    ORDER BY screener.started_at DESC NULLS LAST LIMIT 1;
$$;

CREATE OR REPLACE FUNCTION public.sp_market_screener_context_progress(
    p_user_id uuid, p_context_type varchar, p_context_id uuid
) RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE v_request public.scheduler_manual_run_requests; v_run jsonb; v_execution jsonb;
BEGIN
    PERFORM 1 FROM public.sp_market_screener_context_instruments(
        p_user_id, p_context_type, p_context_id
    );
    SELECT r.* INTO v_request FROM public.scheduler_manual_run_requests r
    JOIN public.scheduler_jobs job ON job.job_id = r.job_id
    WHERE job.job_key = 'market_screener_context_analysis' AND r.requested_by = p_user_id
      AND r.payload->>'context_type' = p_context_type
      AND r.payload->>'context_id' = p_context_id::text
    ORDER BY r.requested_at DESC LIMIT 1;
    SELECT to_jsonb(r) INTO v_run FROM public.market_screener_runs r
    WHERE r.phase_results #>> '{context,user_id}' = p_user_id::text
      AND r.phase_results #>> '{context,context_type}' = p_context_type
      AND r.phase_results #>> '{context,context_id}' = p_context_id::text
    ORDER BY r.started_at DESC, r.screener_run_id DESC LIMIT 1;
    IF v_request.request_id IS NULL OR (
        v_run IS NOT NULL AND (v_run->>'started_at')::timestamptz > v_request.requested_at
        AND v_run->>'job_run_id' IS DISTINCT FROM v_request.job_run_id::text
    ) THEN RETURN v_run; END IF;
    v_execution := public.sp_scheduler_execution_status(v_request.job_run_id, p_user_id);
    RETURN v_execution || jsonb_build_object('screener_run_id', v_execution->'domain_run_id');
END;
$$;

COMMIT;