BEGIN;

SELECT registration.job_id, registration.job_key, registration.action
FROM public.sp_register_scheduler_job(
    p_job_key => 'market_price_refresh_dispatcher',
    p_job_name => 'Market Price Refresh Dispatcher',
    p_python_module => 'jobs.market_price_refresh.worker',
    p_trigger_type => 'interval',
    p_cron_expression => NULL,
    p_interval_minutes => 1,
    p_enabled_by_default => TRUE,
    p_description => 'Discovers stale prices across active market universes and processes the durable refresh queue.'
) AS registration;

COMMIT;