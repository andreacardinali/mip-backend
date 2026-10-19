import importlib
import json
import logging
from pathlib import Path
from typing import Any

from apscheduler.triggers.cron import (
    CronTrigger,
)

from db import SessionLocal
from scheduler_repository import (
    SchedulerRepository,
)

logger = logging.getLogger(__name__)

JOB_DEFINITION_PATTERN = "*.job.json"


class JobDefinitionError(
    RuntimeError,
):
    pass


def discover_job_definitions(
    root: Path,
) -> list[Path]:
    return sorted(path for path in root.rglob(JOB_DEFINITION_PATTERN) if path.is_file())


def load_job_definition(
    path: Path,
    *,
    timezone: str,
) -> dict[str, Any]:
    try:
        definition = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise JobDefinitionError(f"Unable to load {path}") from exc

    validate_job_definition(
        definition,
        path=path,
        timezone=timezone,
    )

    return definition


def validate_job_definition(
    definition: dict[str, Any],
    *,
    path: Path,
    timezone: str,
) -> None:
    required_fields = {
        "schema_version",
        "job_key",
        "job_name",
        "python_module",
        "trigger",
        "enabled_by_default",
    }

    missing_fields = required_fields - definition.keys()

    if missing_fields:
        raise JobDefinitionError(
            f"{path}: missing fields: " f"{sorted(missing_fields)}"
        )

    if definition["schema_version"] != 1:
        raise JobDefinitionError(
            f"{path}: unsupported " "schema_version " f"{definition['schema_version']}"
        )

    for field in (
        "job_key",
        "job_name",
        "python_module",
    ):
        value = definition.get(field)

        if not isinstance(value, str) or not value.strip():
            raise JobDefinitionError(f"{path}: invalid {field}")

    trigger = definition["trigger"]

    if not isinstance(trigger, dict):
        raise JobDefinitionError(f"{path}: trigger must be " "an object")

    trigger_type = str(trigger.get("type", "")).strip().lower()

    if trigger_type == "cron":
        cron_expression = trigger.get("cron_expression")

        if (
            not isinstance(
                cron_expression,
                str,
            )
            or not cron_expression.strip()
        ):
            raise JobDefinitionError(f"{path}: missing " "cron_expression")

        try:
            CronTrigger.from_crontab(
                cron_expression,
                timezone=timezone,
            )
        except Exception as exc:
            raise JobDefinitionError(
                f"{path}: invalid " "cron_expression " f"{cron_expression}"
            ) from exc

        if trigger.get("interval_minutes") is not None:
            raise JobDefinitionError(
                f"{path}: interval_minutes " "is not allowed for cron"
            )

    elif trigger_type == "interval":
        interval_minutes = trigger.get("interval_minutes")

        if (
            not isinstance(
                interval_minutes,
                int,
            )
            or interval_minutes <= 0
        ):
            raise JobDefinitionError(f"{path}: invalid " "interval_minutes")

        if trigger.get("cron_expression") is not None:
            raise JobDefinitionError(
                f"{path}: cron_expression " "is not allowed for interval"
            )

    else:
        raise JobDefinitionError(f"{path}: unsupported " f"trigger type {trigger_type}")

    try:
        module = importlib.import_module(definition["python_module"])
    except Exception as exc:
        raise JobDefinitionError(
            f"{path}: cannot import " f"{definition['python_module']}"
        ) from exc

    run_function = getattr(
        module,
        "run",
        None,
    )

    if not callable(run_function):
        raise JobDefinitionError(
            f"{path}: module " f"{definition['python_module']} " "does not expose run()"
        )


def bootstrap_scheduler_jobs(
    *,
    jobs_root: Path,
    timezone: str,
) -> None:
    definition_paths = discover_job_definitions(jobs_root)

    if not definition_paths:
        raise JobDefinitionError(
            "No scheduler job definitions " f"found under {jobs_root}"
        )

    definitions: list[tuple[Path, dict[str, Any]]] = []

    job_keys: set[str] = set()

    for path in definition_paths:
        definition = load_job_definition(
            path,
            timezone=timezone,
        )

        job_key = definition["job_key"]

        if job_key in job_keys:
            raise JobDefinitionError("Duplicate job_key " f"{job_key} in {path}")

        job_keys.add(job_key)

        definitions.append(
            (
                path,
                definition,
            )
        )

    db = SessionLocal()
    repository = SchedulerRepository(db)

    try:
        for path, definition in definitions:
            trigger = definition["trigger"]

            result = repository.register_job(
                job_key=(definition["job_key"]),
                job_name=(definition["job_name"]),
                python_module=(definition["python_module"]),
                trigger_type=(trigger["type"]),
                cron_expression=(trigger.get("cron_expression")),
                interval_minutes=(trigger.get("interval_minutes")),
                enabled_by_default=(definition["enabled_by_default"]),
            )

            logger.info(
                "Job definition registered "
                "file=%s job_key=%s "
                "job_id=%s action=%s",
                path,
                definition["job_key"],
                result["job_id"],
                result["action"],
            )

        db.commit()

    except Exception:
        db.rollback()

        logger.exception("Scheduler job bootstrap failed")

        raise

    finally:
        db.close()
