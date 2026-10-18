import json
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


class SchedulerRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_enabled_jobs(self) -> list[dict[str, Any]]:
        rows = self.db.execute(text("SELECT * FROM sp_get_enabled_scheduler_jobs()"))
        return [dict(row) for row in rows.mappings().all()]

    def start_run(self, *, job_run_id: UUID, job_id: UUID, started_at: datetime) -> None:
        self.db.execute(
            text("SELECT sp_start_scheduler_job(CAST(:run_id AS uuid), CAST(:job_id AS uuid), :started_at)"),
            {"run_id": str(job_run_id), "job_id": str(job_id), "started_at": started_at},
        )

    def complete_run(self, *, job_run_id: UUID, status: str,
                     result: dict[str, Any] | None, completed_at: datetime,
                     error_message: str | None = None) -> None:
        payload = json.dumps(result, default=str) if result is not None else None
        self.db.execute(
            text("SELECT sp_complete_scheduler_job(CAST(:run_id AS uuid), :status, :error, CAST(:result AS jsonb), :completed_at)"),
            {"run_id": str(job_run_id), "status": status.strip().upper(), "error": error_message,
             "result": payload, "completed_at": completed_at},
        )

    def fail_run(self, *, job_run_id: UUID, error_message: str, completed_at: datetime) -> None:
        self.db.execute(
            text("SELECT sp_fail_scheduler_job(CAST(:run_id AS uuid), :error, :completed_at)"),
            {"run_id": str(job_run_id), "error": error_message, "completed_at": completed_at},
        )
