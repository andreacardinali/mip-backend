import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from db import SessionLocal
from market_data.factory import market_data

logger = logging.getLogger(__name__)

BATCH_SIZE = 5
STALE_PROCESSING_MINUTES = 10
RETRY_DELAYS_SECONDS = (60, 300, 900, 3600, 21600, 86400)


def enqueue_price_refresh(
    db: Session,
    *,
    instrument_id: str | UUID,
    symbol: str,
    data_source: str,
) -> str:
    job_id = db.execute(
        text(
            """
            SELECT sp_enqueue_market_price_refresh(
                :instrument_id,
                :symbol,
                :data_source
            )
            """
        ),
        {
            "instrument_id": str(instrument_id),
            "symbol": symbol.strip().upper(),
            "data_source": (data_source or "YAHOO").strip().upper(),
        },
    ).scalar_one()
    return str(job_id)


def _claim_due_jobs(db: Session, worker_id: str) -> list[dict]:
    db.execute(
        text(
            """
            UPDATE market_price_refresh_queue
            SET status = 'RETRY',
                locked_at = NULL,
                locked_by = NULL,
                next_attempt_at = now(),
                last_error = COALESCE(last_error, 'Recovered stale PROCESSING job'),
                updated_at = now()
            WHERE status = 'PROCESSING'
              AND locked_at < now() - make_interval(mins => :stale_minutes)
            """
        ),
        {"stale_minutes": STALE_PROCESSING_MINUTES},
    )

    rows = db.execute(
        text(
            """
            WITH due AS (
                SELECT job_id
                FROM market_price_refresh_queue
                WHERE status IN ('PENDING', 'RETRY')
                  AND next_attempt_at <= now()
                ORDER BY next_attempt_at, created_at
                FOR UPDATE SKIP LOCKED
                LIMIT :batch_size
            )
            UPDATE market_price_refresh_queue q
            SET status = 'PROCESSING',
                attempt_count = q.attempt_count + 1,
                locked_at = now(),
                locked_by = :worker_id,
                updated_at = now()
            FROM due
            WHERE q.job_id = due.job_id
            RETURNING
                q.job_id,
                q.instrument_id,
                q.symbol,
                q.data_source,
                q.attempt_count,
                q.max_attempts
            """
        ),
        {"batch_size": BATCH_SIZE, "worker_id": worker_id},
    )
    return [dict(row._mapping) for row in rows]


def _complete_job(db: Session, job_id: UUID) -> None:
    db.execute(
        text(
            """
            UPDATE market_price_refresh_queue
            SET status = 'COMPLETED',
                completed_at = now(),
                locked_at = NULL,
                locked_by = NULL,
                last_error = NULL,
                updated_at = now()
            WHERE job_id = :job_id
            """
        ),
        {"job_id": str(job_id)},
    )


def _retry_or_fail_job(
    db: Session,
    *,
    job: dict,
    error_message: str,
) -> str:
    attempt_count = int(job["attempt_count"])
    max_attempts = int(job["max_attempts"])

    if attempt_count >= max_attempts:
        status = "FAILED"
        next_attempt_at = datetime.now(UTC)
        completed_at = datetime.now(UTC)
    else:
        status = "RETRY"
        delay_index = min(attempt_count - 1, len(RETRY_DELAYS_SECONDS) - 1)
        next_attempt_at = datetime.now(UTC) + timedelta(
            seconds=RETRY_DELAYS_SECONDS[delay_index]
        )
        completed_at = None

    db.execute(
        text(
            """
            UPDATE market_price_refresh_queue
            SET status = :status,
                next_attempt_at = :next_attempt_at,
                completed_at = :completed_at,
                locked_at = NULL,
                locked_by = NULL,
                last_error = :last_error,
                updated_at = now()
            WHERE job_id = :job_id
            """
        ),
        {
            "job_id": str(job["job_id"]),
            "status": status,
            "next_attempt_at": next_attempt_at,
            "completed_at": completed_at,
            "last_error": error_message[:4000],
        },
    )
    return status


def _persist_profile_and_complete(job: dict, profile: dict) -> None:
    db = SessionLocal()
    try:
        result = db.execute(
            text(
                """
                SELECT *
                FROM sp_import_asset_profile(
                    CAST(:payload AS jsonb)
                )
                """
            ),
            {"payload": json.dumps(profile)},
        ).first()

        if not result:
            raise RuntimeError("sp_import_asset_profile returned no row")

        _complete_job(db, job["job_id"])
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _record_failure(job: dict, error_message: str) -> str:
    db = SessionLocal()
    try:
        status = _retry_or_fail_job(
            db,
            job=job,
            error_message=error_message,
        )
        db.commit()
        return status
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def process_price_refresh_queue() -> dict:
    worker_id = f"market-price-refresh-{datetime.now(UTC).isoformat()}"
    claim_db = SessionLocal()
    try:
        jobs = _claim_due_jobs(claim_db, worker_id)
        claim_db.commit()
    except Exception:
        claim_db.rollback()
        raise
    finally:
        claim_db.close()

    completed = 0
    retried = 0
    failed = 0
    errors: list[dict] = []

    for job in jobs:
        symbol = str(job["symbol"])
        data_source = str(job["data_source"])
        started_at = datetime.now(UTC)

        try:
            profile = market_data.get_profile(
                symbol,
                data_source=data_source,
                require_price=True,
                price_timeout_seconds=3,
            )

            if not profile or profile.get("last_price") is None:
                raise RuntimeError("Provider returned no market price")

            _persist_profile_and_complete(job, profile)
            completed += 1
            result = "COMPLETED"
        except Exception as exc:
            result = _record_failure(job, str(exc))
            if result == "FAILED":
                failed += 1
            else:
                retried += 1
            errors.append(
                {
                    "job_id": str(job["job_id"]),
                    "symbol": symbol,
                    "attempt": int(job["attempt_count"]),
                    "status": result,
                    "error": str(exc),
                }
            )

        duration_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
        logger.info(
            "Market price refresh job_id=%s symbol=%s data_source=%s "
            "attempt=%s result=%s duration_ms=%s",
            job["job_id"],
            symbol,
            data_source,
            job["attempt_count"],
            result,
            duration_ms,
        )

    status = "SUCCESS"
    if failed or retried:
        status = "PARTIAL"

    return {
        "status": status,
        "claimed": len(jobs),
        "completed": completed,
        "retried": retried,
        "failed": failed,
        "errors": errors,
    }
