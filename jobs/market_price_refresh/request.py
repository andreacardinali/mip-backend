from db import SessionLocal
from job_runtime.requests import current_job_request
from services.market_price_refresh import (
    LIVE_PRICE_MAX_AGE_SECONDS,
    enqueue_user_price_refreshes,
)


def run() -> dict:
    execution = current_job_request.get()
    if not execution or not execution.get("requested_by"):
        raise ValueError("Price refresh requires a scheduler execution request")
    db = SessionLocal()
    try:
        result = enqueue_user_price_refreshes(
            db,
            user_id=execution["requested_by"],
            max_age_seconds=LIVE_PRICE_MAX_AGE_SECONDS,
        )
        db.commit()
        return {
            "status": "SUCCESS",
            "max_age_seconds": LIVE_PRICE_MAX_AGE_SECONDS,
            **result,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
