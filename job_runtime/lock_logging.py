import logging
from contextlib import contextmanager

from sqlalchemy import text

from job_runtime.run_logging import current_job_run_id

logger = logging.getLogger(__name__)


def analysis_lock_owner(operation: str) -> str:
    run_id = current_job_run_id.get()
    return f"mip:run:{run_id}" if run_id else f"mip:{operation}"[:63]


@contextmanager
def lock_identity(connection, owner: str, *, session_scoped: bool = False):
    previous = None
    try:
        with connection.begin_nested():
            previous = connection.execute(
                text("SELECT current_setting('application_name')")
            ).scalar_one()
            connection.execute(
                text("SELECT set_config('application_name', :owner, :local)"),
                {"owner": owner, "local": not session_scoped},
            )
    except Exception:
        logger.warning("Unable to label lock owner owner=%s", owner, exc_info=True)
    try:
        yield
    finally:
        if session_scoped and previous is not None:
            try:
                with connection.begin_nested():
                    connection.execute(
                        text("SELECT set_config('application_name', :owner, false)"),
                        {"owner": previous},
                    )
                    connection.commit()
            except Exception:
                logger.warning("Unable to restore lock owner owner=%s", owner, exc_info=True)
                connection.invalidate()


def log_lock_contention(connection, *, lock_name: str, owner: str, scheduler=False):
    try:
        with connection.begin_nested():
            holders = connection.execute(
                text("""
                    WITH lock_key AS (
                        SELECT CASE WHEN :scheduler
                            THEN hashtextextended(:lock_name, 0)
                            ELSE hashtext(:lock_name)::bigint END AS value
                    )
                    SELECT locks.pid, activity.application_name AS owner,
                           activity.state, activity.xact_start,
                           activity.wait_event_type, activity.wait_event,
                           runs.job_run_id AS run_id, jobs.job_id,
                           jobs.job_name, jobs.python_module AS module
                    FROM pg_locks AS locks
                    JOIN pg_stat_activity AS activity ON activity.pid = locks.pid
                    CROSS JOIN lock_key
                    LEFT JOIN public.scheduler_job_runs AS runs
                        ON activity.application_name = 'mip:run:' || runs.job_run_id::text
                    LEFT JOIN public.scheduler_jobs AS jobs ON jobs.job_id = runs.job_id
                    WHERE locks.locktype = 'advisory' AND locks.granted
                      AND locks.database = (SELECT oid FROM pg_database WHERE datname = current_database())
                      AND locks.objsubid = 1
                      AND locks.classid = ((lock_key.value >> 32) & 4294967295)::oid
                      AND locks.objid = (lock_key.value & 4294967295)::oid
                """),
                {"lock_name": lock_name, "scheduler": scheduler},
            ).mappings().all()
        logger.warning(
            "Lock contention lock=%s requester=%s blockers=%s",
            lock_name, owner, [dict(holder) for holder in holders] or "released_or_not_visible",
        )
    except Exception:
        logger.warning(
            "Lock contention lock=%s requester=%s blockers=unavailable",
            lock_name, owner, exc_info=True,
        )