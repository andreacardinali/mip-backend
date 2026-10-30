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

    def claim_manual_runs(self, limit: int = 5) -> list[dict[str, Any]]:
        rows = self.db.execute(
            text("SELECT * FROM public.sp_scheduler_claim_execution_requests(:limit)"),
            {"limit": limit},
        )
        return [dict(row) for row in rows.mappings().all()]

    def request_run(
        self, *, job_key: str, requested_by: str, payload: dict[str, Any], source: str
    ) -> dict[str, Any]:
        row = self.db.execute(
            text(
                "SELECT * FROM public.sp_scheduler_request_execution("
                ":job_key, CAST(:requested_by AS uuid), CAST(:payload AS jsonb), :source)"
            ),
            {
                "job_key": job_key,
                "requested_by": requested_by,
                "payload": json.dumps(payload),
                "source": source,
            },
        ).mappings().one()
        return dict(row)

    def request_status(self, *, job_run_id: str, user_id: str):
        return self.db.execute(
            text(
                "SELECT public.sp_scheduler_execution_status("
                "CAST(:job_run_id AS uuid), CAST(:user_id AS uuid))"
            ),
            {"job_run_id": job_run_id, "user_id": user_id},
        ).scalar_one_or_none()

    def complete_manual_request(self, job_run_id: UUID) -> None:
        self.db.execute(
            text(
                """
                SELECT public.sp_scheduler_complete_job_run_request(
                    CAST(:job_run_id AS uuid)
                )
                """
            ),
            {"job_run_id": str(job_run_id)},
        )

    def defer_manual_request(self, job_run_id: UUID) -> None:
        self.db.execute(
            text(
                """
                SELECT public.sp_scheduler_defer_job_run_request(
                    CAST(:job_run_id AS uuid)
                )
                """
            ),
            {"job_run_id": str(job_run_id)},
        )

    def fail_manual_request(self, job_run_id: UUID, error_message: str) -> None:
        self.db.execute(
            text(
                """
                SELECT public.sp_scheduler_fail_job_run_request(
                    CAST(:job_run_id AS uuid),
                    :error_message
                )
                """
            ),
            {
                "job_run_id": str(job_run_id),
                "error_message": error_message,
            },
        )

    def update_next_run(
        self,
        *,
        job_id: UUID,
        next_run_at: datetime | None,
    ) -> None:
        self.db.execute(
            text(
                """
                SELECT sp_scheduler_update_next_run(
                    CAST(:job_id AS uuid),
                    :next_run_at
                )
                """
            ),
            {
                "job_id": str(job_id),
                "next_run_at": next_run_at,
            },
        )

    def start_run(
        self,
        *,
        job_run_id: UUID,
        job_id: UUID,
        started_at: datetime,
    ) -> None:
        self.db.execute(
            text(
                """
                SELECT sp_start_scheduler_job(
                    CAST(:run_id AS uuid),
                    CAST(:job_id AS uuid),
                    :started_at
                )
                """
            ),
            {
                "run_id": str(job_run_id),
                "job_id": str(job_id),
                "started_at": started_at,
            },
        )

    def complete_run(
        self,
        *,
        job_run_id: UUID,
        status: str,
        result: dict[str, Any] | None,
        completed_at: datetime,
        error_message: str | None = None,
    ) -> None:
        database_status = status.strip().upper()
        if database_status == "INCOMPLETE":
            database_status = "PARTIAL"
        elif database_status == "SKIPPED":
            database_status = "SUCCESS"
        payload = json.dumps(result, default=str) if result is not None else None
        self.db.execute(
            text(
                """
                SELECT sp_complete_scheduler_job(
                    CAST(:run_id AS uuid),
                    :status,
                    :error,
                    CAST(:result AS jsonb),
                    :completed_at
                )
                """
            ),
            {
                "run_id": str(job_run_id),
                "status": database_status,
                "error": error_message,
                "result": payload,
                "completed_at": completed_at,
            },
        )

    def fail_run(
        self,
        *,
        job_run_id: UUID,
        error_message: str,
        completed_at: datetime,
    ) -> None:
        self.db.execute(
            text(
                """
                SELECT sp_fail_scheduler_job(
                    CAST(:run_id AS uuid),
                    :error,
                    :completed_at
                )
                """
            ),
            {
                "run_id": str(job_run_id),
                "error": error_message,
                "completed_at": completed_at,
            },
        )

    def register_job(
        self,
        *,
        job_key: str,
        job_name: str,
        python_module: str,
        trigger_type: str,
        cron_expression: str | None,
        interval_minutes: int | None,
        enabled_by_default: bool,
    ) -> dict[str, Any]:
        row = (
            self.db.execute(
                text(
                    """
                    SELECT *
                    FROM sp_register_scheduler_job(
                        :job_key,
                        :job_name,
                        :python_module,
                        :trigger_type,
                        :cron_expression,
                        :interval_minutes,
                        :enabled_by_default
                    )
                    """
                ),
                {
                    "job_key": job_key,
                    "job_name": job_name,
                    "python_module": python_module,
                    "trigger_type": trigger_type,
                    "cron_expression": cron_expression,
                    "interval_minutes": interval_minutes,
                    "enabled_by_default": enabled_by_default,
                },
            )
            .mappings()
            .one()
        )
        return dict(row)
