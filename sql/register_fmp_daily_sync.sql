INSERT INTO scheduler_jobs
(
    job_id,
    job_name,
    python_module,
    trigger_type,
    cron_expression,
    interval_minutes,
    enabled
)
VALUES
(
    gen_random_uuid(),
    'FMP Daily Sync',
    'jobs.fmp_daily_sync',
    'cron',
    '15 06 * * *',
    NULL,
    TRUE
)
ON CONFLICT DO NOTHING;
