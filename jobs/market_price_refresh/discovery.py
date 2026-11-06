from db import SessionLocal
from services.market_price_refresh import enqueue_stale_market_price_refreshes


def run() -> dict:
    db = SessionLocal()
    try:
        result = enqueue_stale_market_price_refreshes(db)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()