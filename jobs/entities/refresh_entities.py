from db import SessionLocal
from services.entities import RefreshMode, refresh_entities


def run() -> dict:
    db = SessionLocal()
    try:
        return refresh_entities(
            db,
            mode=RefreshMode.MISSING,
            symbol=None,
            create_if_missing=False,
            full_refresh=False,
        )
    finally:
        db.close()
