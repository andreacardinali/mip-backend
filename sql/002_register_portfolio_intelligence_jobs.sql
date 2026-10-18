INSERT INTO scheduler_jobs(job_id,job_name,python_module,trigger_type,cron_expression,enabled)
SELECT gen_random_uuid(),'Portfolio Intelligence - Daily Movers','jobs.portfolio_intelligence.daily_mover','cron','0,30 9-21 * * *',TRUE
WHERE NOT EXISTS (SELECT 1 FROM scheduler_jobs WHERE python_module='jobs.portfolio_intelligence.daily_mover');
INSERT INTO scheduler_jobs(job_id,job_name,python_module,trigger_type,cron_expression,enabled)
SELECT gen_random_uuid(),'Portfolio Intelligence - Upcoming Catalysts','jobs.portfolio_intelligence.upcoming_catalyst','cron','15 7 * * *',TRUE
WHERE NOT EXISTS (SELECT 1 FROM scheduler_jobs WHERE python_module='jobs.portfolio_intelligence.upcoming_catalyst');
INSERT INTO scheduler_jobs(job_id,job_name,python_module,trigger_type,cron_expression,enabled)
SELECT gen_random_uuid(),'Portfolio Intelligence - Breaking Catalysts','jobs.portfolio_intelligence.breaking_catalyst','cron','0,30 9-21 * * *',TRUE
WHERE NOT EXISTS (SELECT 1 FROM scheduler_jobs WHERE python_module='jobs.portfolio_intelligence.breaking_catalyst');
