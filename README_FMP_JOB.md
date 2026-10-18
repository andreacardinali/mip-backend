# FMP Daily Sync job

## Files to copy

Copy `jobs/` into the repository root and replace the existing `scheduler.py`.

## Configuration

Merge the variables from `env.fmp.example` into the existing master `.env` file.

## Register the job

Run:

```bash
psql "$DATABASE_URL" -f sql/register_fmp_daily_sync.sql
```

If `DATABASE_URL` exists only inside `.env`, use the same PostgreSQL command and credentials already used for the MIP database.

## Manual test

From the backend virtual environment:

```bash
python -m jobs.fmp_daily_sync
```

The command prints a result containing `SUCCESS`, `PARTIAL`, or `FAILED` and the counters for each FMP source.

## Scheduler test

Restart `mip-scheduler`, then inspect the service log and the latest rows:

```sql
SELECT
    job_run_id,
    job_id,
    started_at,
    completed_at,
    status,
    error_message,
    result
FROM scheduler_job_runs
ORDER BY started_at DESC
LIMIT 10;
```

Disable the n8n workflow only after the manual run and at least one scheduled run complete correctly.
