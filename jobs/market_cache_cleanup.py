from sqlalchemy import text

from db import SessionLocal


def run() -> dict:
    db = SessionLocal()
    try:
        deleted = db.execute(text("SELECT sp_cleanup_market_cache(30)")).scalar()
        db.commit()
        return {"status": "SUCCESS", "deleted": deleted}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()