import importlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text

from config.settings import settings
from db import SessionLocal
from scheduler_bootstrap import bootstrap_scheduler_jobs
from scheduler_repository import SchedulerRepository

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

SCHEDULER_TIMEZONE = settings.get("SCHEDULER_TIMEZONE", "Europe/Rome")
scheduler = BlockingScheduler(timezone=SCHEDULER_TIMEZONE)
JOBS_ROOT = Path(__file__).resolve().parent / "jobs"
SYNC_JOB_ID = "__scheduler_configuration_sync__"


def normalize_result(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {"status": "SUCCESS", "value": value}


def execute_job(job_id: str, python_module: str) -> dict[str, Any]:
    db = SessionLocal()
    repository = SchedulerRepository(db)
    job_uuid = UUID(job_id)
    job_run_id = uuid4()
    started_at = datetime.now(UTC)
    run_started = False

    try:
        repository.start_run(
            job_run_id=job_run_id,
            job_id=job_uuid,
            started_at=started_at,
        )
        db.commit()
        run_started = True

        module = importlib.import_module(python_module)
        run_function = getattr(module, "run", None)
        if not callable(run_function):
            raise RuntimeError(
                f"Module {python_module} does not expose a callable run()"
            )

        result = normalize_result(run_function())
        result_status = str(result.get("status", "SUCCESS")).upper()
        supported_statuses = {
            "SUCCESS",
            "PARTIAL",
            "FAILED",
            "INCOMPLETE",
        }
        if result_status not in supported_statuses:
            raise ValueError(f"Unsupported job result status: {result_status}")

        error_message = None
        if result_status == "FAILED":
            error_message = "Job completed with FAILED status"
        elif result_status in {"PARTIAL", "INCOMPLETE"}:
            error_message = "Job completed with partial or incomplete result"

        repository.complete_run(
            job_run_id=job_run_id,
            status=result_status,
            error_message=error_message,
            result=result,
            completed_at=datetime.now(UTC),
        )
        db.commit()

        logger.info(
            "Job completed job_id=%s run_id=%s status=%s",
            job_id,
            job_run_id,
            result_status,
        )
        return result

    except Exception as error:
        db.rollback()
        if run_started:
            try:
                repository.fail_run(
                    job_run_id=job_run_id,
                    error_message=str(error),
                    completed_at=datetime.now(UTC),
                )
                db.commit()
            except Exception:
                db.rollback()
                logger.exception(
                    "Unable to persist scheduler failure " "job_id=%s run_id=%s",
                    job_id,
                    job_run_id,
                )

        logger.exception(
            "Job failed job_id=%s run_id=%s module=%s",
            job_id,
            job_run_id,
            python_module,
        )
        raise
    finally:
        db.close()


def _build_trigger(job: dict[str, Any]) -> BaseTrigger:
    job_name = str(job["job_name"])
    trigger_type = str(job["trigger_type"]).strip().lower()

    if trigger_type == "interval":
        interval_minutes = job.get("interval_minutes")
        if interval_minutes is None or int(interval_minutes) <= 0:
            raise ValueError(f"Invalid interval_minutes for job {job_name}")
        return IntervalTrigger(
            minutes=int(interval_minutes),
            timezone=SCHEDULER_TIMEZONE,
        )

    if trigger_type == "cron":
        cron_expression = job.get("cron_expression")
        if not cron_expression:
            raise ValueError(f"Missing cron_expression for job {job_name}")
        return CronTrigger.from_crontab(
            str(cron_expression),
            timezone=SCHEDULER_TIMEZONE,
        )

    raise ValueError(f"Unsupported trigger type for job {job_name}: {trigger_type}")


def _job_requires_update(
    current_job: Any,
    desired_job: dict[str, Any],
    desired_trigger: BaseTrigger,
) -> bool:
    desired_args = (
        str(desired_job["job_id"]),
        str(desired_job["python_module"]),
    )

    return any(
        (
            current_job.name != str(desired_job["job_name"]),
            tuple(current_job.args) != desired_args,
            str(current_job.trigger) != str(desired_trigger),
        )
    )


def _add_or_update_job(
    job: dict[str, Any],
    trigger: BaseTrigger,
) -> Any:
    job_id = str(job["job_id"])
    current_job = scheduler.get_job(job_id)

    if current_job is None:
        scheduled_job = scheduler.add_job(
            func=execute_job,
            trigger=trigger,
            args=[job_id, str(job["python_module"])],
            id=job_id,
            name=str(job["job_name"]),
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info(
            "Added scheduler job job_id=%s name=%s",
            job_id,
            job["job_name"],
        )
        return scheduled_job

    if _job_requires_update(current_job, job, trigger):
        scheduled_job = scheduler.reschedule_job(
            job_id,
            trigger=trigger,
        )
        scheduled_job.modify(
            name=str(job["job_name"]),
            args=[job_id, str(job["python_module"])],
        )
        logger.info(
            "Updated scheduler job job_id=%s name=%s",
            job_id,
            job["job_name"],
        )
        return scheduled_job

    return current_job


def _next_run_time(
    scheduled_job: Any,
    trigger: BaseTrigger,
) -> datetime | None:
    next_run_time = getattr(scheduled_job, "next_run_time", None)
    if next_run_time is not None:
        return next_run_time

    now = datetime.now(scheduler.timezone)
    return trigger.get_next_fire_time(None, now)


def recover_stale_runs() -> int:
    db = SessionLocal()

    try:
        recovered = db.execute(
            text(
                """
                SELECT public.sp_scheduler_fail_stale_runs(
                    CAST(:stale_after AS interval)
                )
                """
            ),
            {
                "stale_after": "30 minutes",
            },
        ).scalar_one()

        db.commit()

        logger.info(
            "Recovered stale scheduler runs count=%s",
            recovered,
        )

        return int(recovered)

    except Exception:
        db.rollback()
        logger.exception(
            "Unable to recover stale scheduler runs " "during scheduler startup"
        )
        return 0

    finally:
        db.close()


def sync_jobs() -> None:
    db = SessionLocal()
    repository = SchedulerRepository(db)

    try:
        desired_jobs = {
            str(job["job_id"]): job for job in repository.get_enabled_jobs()
        }
        current_jobs = {
            item.id: item for item in scheduler.get_jobs() if item.id != SYNC_JOB_ID
        }

        for job_id in set(current_jobs) - set(desired_jobs):
            scheduler.remove_job(job_id)
            repository.update_next_run(
                job_id=UUID(job_id),
                next_run_at=None,
            )
            logger.info(
                "Removed disabled scheduler job job_id=%s",
                job_id,
            )

        for job_id, job in desired_jobs.items():
            trigger = _build_trigger(job)
            scheduled_job = _add_or_update_job(job, trigger)
            repository.update_next_run(
                job_id=UUID(job_id),
                next_run_at=_next_run_time(
                    scheduled_job,
                    trigger,
                ),
            )

        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Unable to synchronize scheduler jobs")
        raise
    finally:
        db.close()


def main() -> None:
    bootstrap_scheduler_jobs(
        jobs_root=JOBS_ROOT,
        timezone=SCHEDULER_TIMEZONE,
    )

    recover_stale_runs()

    sync_jobs()

    scheduler.add_job(
        sync_jobs,
        trigger="interval",
        seconds=30,
        id=SYNC_JOB_ID,
        name="Scheduler configuration sync",
        max_instances=1,
        coalesce=True,
    )

    logger.info(
        "Scheduler started timezone=%s jobs=%s",
        SCHEDULER_TIMEZONE,
        len(scheduler.get_jobs()) - 1,
    )

    scheduler.start()


if __name__ == "__main__":
    main()
