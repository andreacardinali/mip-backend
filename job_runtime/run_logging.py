import contextvars
import logging
import threading
from uuid import UUID

from sqlalchemy import text

from db import SessionLocal

current_job_run_id: contextvars.ContextVar[UUID | None] = contextvars.ContextVar(
    "current_job_run_id",
    default=None,
)
_write_guard = threading.local()


class SchedulerRunLogHandler(logging.Handler):
    """Persists log records emitted while a scheduler run is active."""

    def emit(self, record: logging.LogRecord) -> None:
        job_run_id = current_job_run_id.get()
        if job_run_id is None or getattr(_write_guard, "active", False):
            return

        _write_guard.active = True
        db = SessionLocal()
        try:
            db.execute(
                text(
                    """
                    SELECT public.sp_scheduler_append_run_log(
                        CAST(:job_run_id AS uuid),
                        :level,
                        :logger_name,
                        :message
                    )
                    """
                ),
                {
                    "job_run_id": str(job_run_id),
                    "level": record.levelname,
                    "logger_name": record.name,
                    "message": self.format(record),
                },
            )
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
            _write_guard.active = False


def install_scheduler_run_log_handler() -> None:
    root_logger = logging.getLogger()
    if any(
        isinstance(handler, SchedulerRunLogHandler) for handler in root_logger.handlers
    ):
        return

    handler = SchedulerRunLogHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(handler)
