from db import SessionLocal
from services.market_price_refresh import (
    enqueue_stale_market_price_refreshes,
    process_price_refresh_queue,
)


def run() -> dict:
    db = SessionLocal()
    try:
        discovery = enqueue_stale_market_price_refreshes(db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    return {
        **process_price_refresh_queue(),
        "candidates_discovered": discovery["candidates_count"],
        "candidates_enqueued": discovery["enqueued_count"],
    }
