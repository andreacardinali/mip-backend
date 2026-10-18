import importlib
import json
import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text

from config.settings import settings

from db import SessionLocal

from scheduler_repository import SchedulerRepository

db = SessionLocal()
repository = SchedulerRepository(db)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger(__name__)

SCHEDULER_TIMEZONE = settings.get(
    "SCHEDULER_TIMEZONE",
    "Europe/Rome",
)

scheduler = BlockingScheduler(
    timezone=SCHEDULER_TIMEZONE,
)


def execute_job(
    job_id: str,
    python_module: str,
):
    db = SessionLocal()
    job_run_id = uuid4()
    started_at = datetime.now(timezone.utc)

    try:
        repository.start_run(
            job_run_id=job_run_id,
            job_id=UUID(job_id),
            started_at=started_at,
        )

        db.commit()

        module = importlib.import_module(
            python_module
        )
        run_function = getattr(module, "run")
        result = run_function()

        result_status = (
            result.get("status", "SUCCESS")
            if isinstance(result, dict)
            else "SUCCESS"
        )

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
            completed_at=datetime.now(
                timezone.utc
            ),
        )

        db.commit()

        logger.info(
            "Job completed job_id=%s status=%s",
            job_id,
            result_status,
        )

        return result

    except Exception as exc:
        db.rollback()

        repository.fail_run(
            job_run_id=job_run_id,
            error_message=str(exc),
            completed_at=datetime.now(
                timezone.utc
            ),
        )

        db.commit()

        logger.exception(
            "Job failed job_id=%s module=%s",
            job_id,
            python_module,
        )
        raise

    finally:
        db.close()


def load_jobs():
    db = SessionLocal()

    try:
        rows = db.execute(
            text(
                """
                SELECT
                    job_id,
                    job_name,
                    python_module,
                    trigger_type,
                    cron_expression,
                    interval_minutes
                FROM scheduler_jobs
                WHERE enabled = true
                """
            )
        ).fetchall()

        for row in rows:
            job = row._mapping
            trigger_type = str(
                job["trigger_type"]
            ).lower()

            common_args = {
                "func": execute_job,
                "args": [
                    str(job["job_id"]),
                    job["python_module"],
                ],
                "id": str(job["job_id"]),
                "name": job["job_name"],
                "replace_existing": True,
                "max_instances": 1,
                "coalesce": True,
            }

            if trigger_type == "interval":
                scheduler.add_job(
                    trigger="interval",
                    minutes=job["interval_minutes"],
                    **common_args,
                )

            elif trigger_type == "cron":
                scheduler.add_job(
                    trigger=CronTrigger.from_crontab(
                        job["cron_expression"],
                        timezone=SCHEDULER_TIMEZONE,
                    ),
                    **common_args,
                )

            else:
                logger.error(
                    "Unsupported trigger type job=%s trigger=%s",
                    job["job_name"],
                    trigger_type,
                )
                continue

            logger.info(
                "Loaded job: %s",
                job["job_name"],
            )

    finally:
        db.close()


def main():
    load_jobs()
    logger.info(
        "Scheduler started timezone=%s",
        SCHEDULER_TIMEZONE,
    )
    scheduler.start()


if __name__ == "__main__":
    main()
