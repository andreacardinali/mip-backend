import json
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


class SchedulerRepository:
    """
    Repository per l'accesso alle stored procedure
    utilizzate dal servizio mip-scheduler.

    Il repository non esegue commit o rollback.
    La gestione delle transazioni resta responsabilità
    del chiamante.
    """

    def __init__(
        self,
        db: Session,
    ) -> None:
        self.db = db

    def get_enabled_jobs(
        self,
    ) -> list[dict[str, Any]\]:
        """
        Restituisce tutti i job scheduler abilitati.

        Stored procedure richiesta:

            sp_get_enabled_scheduler_jobs()
        """

        rows = self.db.execute(
            text(
                """
                SELECT *
                FROM sp_get_enabled_scheduler_jobs()
                """
            )
        ).mappings().all()

        return [
            dict(row)
            for row in rows
        ]

    def start_run(
        self,
        *,
        job_run_id: UUID,
        job_id: UUID,
        started_at: datetime,
    ) -> None:
        """
        Registra l'avvio di un'esecuzione.

        Stored procedure richiesta:

            sp_start_scheduler_job(
                p_job_run_id uuid,
                p_job_id uuid,
                p_started_at timestamptz
            )
        """

        self.db.execute(
            text(
                """
                SELECT sp_start_scheduler_job(
                    p_job_run_id => CAST(
                        :job_run_id AS uuid
                    ),
                    p_job_id => CAST(
                        :job_id AS uuid
                    ),
                    p_started_at => :started_at
                )
                """
            ),
            {
                "job_run_id": str(
                    job_run_id
                ),
                "job_id": str(
                    job_id
                ),
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
        """
        Completa un'esecuzione con stato SUCCESS,
        PARTIAL o FAILED.

        La stored procedure deve:

        1. aggiornare scheduler_job_runs;
        2. ricavare il job_id dalla relativa esecuzione;
        3. aggiornare scheduler_jobs.last_run_at.

        Stored procedure richiesta:

            sp_complete_scheduler_job(
                p_job_run_id uuid,
                p_status varchar,
                p_error_message text,
                p_result jsonb,
                p_completed_at timestamptz
            )
        """

        serialized_result = (
            json.dumps(
                result,
                default=str,
            )
            if result is not None
            else None
        )

        self.db.execute(
            text(
                """
                SELECT sp_complete_scheduler_job(
                    p_job_run_id => CAST(
                        :job_run_id AS uuid
                    ),
                    p_status => :status,
                    p_error_message => :error_message,
                    p_result => CAST(
                        :result AS jsonb
                    ),
                    p_completed_at => :completed_at
                )
                """
            ),
            {
                "job_run_id": str(
                    job_run_id
                ),
                "status": status,
                "error_message": error_message,
                "result": serialized_result,
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
        """
        Registra un errore non gestito durante
        l'esecuzione del job.

        La stored procedure deve impostare:

            status = 'FAILED'
            completed_at = p_completed_at
            error_message = p_error_message

        Stored procedure richiesta:

            sp_fail_scheduler_job(
                p_job_run_id uuid,
                p_error_message text,
                p_completed_at timestamptz
            )
        """

        self.db.execute(
            text(
                """
                SELECT sp_fail_scheduler_job(
                    p_job_run_id => CAST(
                        :job_run_id AS uuid
                    ),
                    p_error_message => :error_message,
                    p_completed_at => :completed_at
                )
                """
            ),
            {
                "job_run_id": str(
                    job_run_id
                ),
                "error_message": error_message,
                "completed_at": completed_at,
            },
        )