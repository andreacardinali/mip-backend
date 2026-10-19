import importlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from config.settings import settings
from db import SessionLocal
from scheduler_bootstrap import (
    bootstrap_scheduler_jobs,
)
from scheduler_repository import SchedulerRepository

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
SCHEDULER_TIMEZONE = settings.get("SCHEDULER_TIMEZONE", "Europe/Rome")
scheduler = BlockingScheduler(timezone=SCHEDULER_TIMEZONE)

JOBS_ROOT = Path(__file__).resolve().parent / "jobs"


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
            job_run_id=job_run_id, job_id=job_uuid, started_at=started_at
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
        if result_status not in {"SUCCESS", "PARTIAL", "FAILED"}:
            raise ValueError(f"Unsupported job result status: {result_status}")

        error_message = None
        if result_status == "FAILED":
            error_message = "Job completed with FAILED status"
        elif result_status == "PARTIAL":
            error_message = "Job completed with partial failures"

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

    except Exception as exc:
        db.rollback()
        if run_started:
            try:
                repository.fail_run(
                    job_run_id=job_run_id,
                    error_message=str(exc),
                    completed_at=datetime.now(UTC),
                )
                db.commit()
            except Exception:
                db.rollback()
                logger.exception(
                    "Unable to persist scheduler failure job_id=%s run_id=%s",
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


def load_jobs() -> None:
    db = SessionLocal()
    repository = SchedulerRepository(db)
    try:
        for job in repository.get_enabled_jobs():
            job_id = str(job["job_id"])
            job_name = str(job["job_name"])
            python_module = str(job["python_module"])
            trigger_type = str(job["trigger_type"]).strip().lower()
            common_args = {
                "func": execute_job,
                "args": [job_id, python_module],
                "id": job_id,
                "name": job_name,
                "replace_existing": True,
                "max_instances": 1,
                "coalesce": True,
            }
            if trigger_type == "interval":
                interval_minutes = job.get("interval_minutes")
                if interval_minutes is None or int(interval_minutes) <= 0:
                    raise ValueError(f"Invalid interval_minutes for job {job_name}")
                scheduler.add_job(
                    trigger="interval", minutes=int(interval_minutes), **common_args
                )
            elif trigger_type == "cron":
                cron_expression = job.get("cron_expression")
                if not cron_expression:
                    raise ValueError(f"Missing cron_expression for job {job_name}")
                scheduler.add_job(
                    trigger=CronTrigger.from_crontab(
                        str(cron_expression), timezone=SCHEDULER_TIMEZONE
                    ),
                    **common_args,
                )
            else:
                logger.error(
                    "Unsupported trigger type job=%s trigger=%s", job_name, trigger_type
                )
                continue
            logger.info(
                "Loaded job job_id=%s name=%s trigger=%s",
                job_id,
                job_name,
                trigger_type,
            )
    finally:
        db.close()


def main() -> None:
    bootstrap_scheduler_jobs(
        jobs_root=JOBS_ROOT,
        timezone=SCHEDULER_TIMEZONE,
    )

    load_jobs()

    logger.info(
        "Scheduler started " "timezone=%s jobs=%s",
        SCHEDULER_TIMEZONE,
        len(scheduler.get_jobs()),
    )

    scheduler.start()


if __name__ == "__main__":
    main()
