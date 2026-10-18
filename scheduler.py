import importlib
import logging

from apscheduler.schedulers.blocking import (
    BlockingScheduler,
)

from apscheduler.triggers.cron import (
    CronTrigger,
)

from sqlalchemy import text

from db import SessionLocal

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s "
        "%(levelname)s "
        "%(message)s"
    ),
)

scheduler = BlockingScheduler()


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

            module = (
                importlib.import_module(
                    job["python_module"]
                )
            )

            run_function = getattr(
                module,
                "run"
            )

            if (
                job["trigger_type"]
                == "interval"
            ):

                scheduler.add_job(
                    run_function,
                    trigger="interval",
                    minutes=job[
                        "interval_minutes"
                    ],
                    id=str(job["job_id"]),
                    replace_existing=True,
                )

            elif (
                job["trigger_type"]
                == "cron"
            ):

                scheduler.add_job(
                    run_function,
                    trigger=CronTrigger.from_crontab(
                        job[
                            "cron_expression"
                        ]
                    ),
                    id=str(job["job_id"]),
                    replace_existing=True,
                )

            logging.info(
                "Loaded job: %s",
                job["job_name"],
            )

    finally:

        db.close()


def main():

    load_jobs()

    logging.info(
        "Scheduler started"
    )

    scheduler.start()


if __name__ == "__main__":
    main()