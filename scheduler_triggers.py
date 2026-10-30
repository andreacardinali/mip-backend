from typing import Any

from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config.settings import settings

SCHEDULER_TIMEZONE = settings.get("SCHEDULER_TIMEZONE", "Europe/Rome")


def build_trigger(job: dict[str, Any]) -> BaseTrigger:
    job_name = str(job.get("job_name", "preview"))
    trigger_type = str(job["trigger_type"]).strip().lower()
    if trigger_type == "interval":
        minutes = job.get("interval_minutes")
        if minutes is None or int(minutes) <= 0:
            raise ValueError(f"Invalid interval_minutes for job {job_name}")
        return IntervalTrigger(minutes=int(minutes), timezone=SCHEDULER_TIMEZONE)
    if trigger_type == "cron":
        expression = job.get("cron_expression")
        if not expression:
            raise ValueError(f"Missing cron_expression for job {job_name}")
        return CronTrigger.from_crontab(str(expression), timezone=SCHEDULER_TIMEZONE)
    raise ValueError(f"Unsupported trigger type for job {job_name}: {trigger_type}")
