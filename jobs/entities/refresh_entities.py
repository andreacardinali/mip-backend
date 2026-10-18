from db import SessionLocal
from services.entities import RefreshMode, refresh_entities


def run() -> dict:
    db = SessionLocal()

    try:
        result = refresh_entities(
            db,
            mode=RefreshMode.MISSING,
            symbol=None,
            create_if_missing=False,
            full_refresh=False,
        )
        db.commit()
        return result

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()
