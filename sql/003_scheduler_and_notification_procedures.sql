BEGIN;

CREATE OR REPLACE FUNCTION sp_get_enabled_scheduler_jobs()
RETURNS TABLE(job_id uuid, job_name varchar, python_module varchar, trigger_type varchar,
              cron_expression varchar, interval_minutes integer)
LANGUAGE sql STABLE AS $$
SELECT sj.job_id, sj.job_name, sj.python_module, sj.trigger_type,
       sj.cron_expression, sj.interval_minutes
FROM scheduler_jobs sj
WHERE sj.enabled = true
ORDER BY sj.job_name;
$$;

CREATE OR REPLACE FUNCTION sp_start_scheduler_job(p_job_run_id uuid, p_job_id uuid, p_started_at timestamptz)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO scheduler_job_runs(job_run_id, job_id, started_at, status)
    VALUES (p_job_run_id, p_job_id, p_started_at, 'RUNNING');
END;
$$;

CREATE OR REPLACE FUNCTION sp_complete_scheduler_job(
    p_job_run_id uuid, p_status varchar, p_error_message text,
    p_result jsonb, p_completed_at timestamptz)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE v_job_id uuid; v_status varchar;
BEGIN
    v_status := UPPER(TRIM(p_status));
    IF v_status NOT IN ('SUCCESS', 'PARTIAL', 'FAILED') THEN
        RAISE EXCEPTION 'Invalid scheduler status: %', p_status;
    END IF;
    UPDATE scheduler_job_runs
    SET completed_at=p_completed_at, status=v_status,
        error_message=p_error_message, result=p_result
    WHERE job_run_id=p_job_run_id RETURNING job_id INTO v_job_id;
    IF v_job_id IS NULL THEN RAISE EXCEPTION 'Scheduler run not found: %', p_job_run_id; END IF;
    UPDATE scheduler_jobs SET last_run_at=p_completed_at WHERE job_id=v_job_id;
END;
$$;

CREATE OR REPLACE FUNCTION sp_fail_scheduler_job(p_job_run_id uuid, p_error_message text, p_completed_at timestamptz)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE v_job_id uuid;
BEGIN
    UPDATE scheduler_job_runs
    SET completed_at=p_completed_at, status='FAILED', error_message=p_error_message
    WHERE job_run_id=p_job_run_id RETURNING job_id INTO v_job_id;
    IF v_job_id IS NULL THEN RAISE EXCEPTION 'Scheduler run not found: %', p_job_run_id; END IF;
    UPDATE scheduler_jobs SET last_run_at=p_completed_at WHERE job_id=v_job_id;
END;
$$;

CREATE OR REPLACE FUNCTION sp_notification_exists(p_user_id uuid, p_notification_key varchar)
RETURNS boolean LANGUAGE sql STABLE AS $$
SELECT EXISTS(SELECT 1 FROM notifications n
              WHERE n.user_id=p_user_id AND n.notification_key=p_notification_key AND n.status='SENT');
$$;

CREATE OR REPLACE FUNCTION sp_upsert_notification(
    p_user_id uuid, p_notification_key varchar, p_branch varchar, p_entity_id uuid,
    p_symbol varchar, p_event_key varchar, p_severity varchar, p_title text,
    p_message text, p_context jsonb, p_status varchar, p_sent_at timestamptz)
RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE v_notification_id uuid;
BEGIN
    INSERT INTO notifications(user_id, entity_id, notification_type, notification_key,
        branch, symbol, event_key, severity, status, title, message, context, observed_at, sent_at)
    VALUES(p_user_id, p_entity_id, p_branch, p_notification_key, p_branch, p_symbol,
        p_event_key, p_severity, UPPER(p_status), p_title, p_message,
        COALESCE(p_context, '{}'::jsonb), now(), p_sent_at)
    ON CONFLICT (user_id, notification_key) WHERE notification_key IS NOT NULL
    DO UPDATE SET status=EXCLUDED.status, message=EXCLUDED.message,
                  context=EXCLUDED.context, sent_at=EXCLUDED.sent_at
    RETURNING notification_id INTO v_notification_id;
    RETURN v_notification_id;
END;
$$;

CREATE OR REPLACE FUNCTION sp_purge_notifications(p_days integer)
RETURNS integer LANGUAGE plpgsql AS $$
DECLARE v_deleted integer;
BEGIN
    IF p_days < 1 THEN RAISE EXCEPTION 'p_days must be >= 1'; END IF;
    DELETE FROM notifications
    WHERE status='SENT'
      AND sent_at < now() - make_interval(days => p_days)
      AND branch IN ('DAILY_MOVER','UPCOMING_CATALYST','BREAKING_CATALYST');
    GET DIAGNOSTICS v_deleted = ROW_COUNT;
    RETURN v_deleted;
END;
$$;

COMMIT;
