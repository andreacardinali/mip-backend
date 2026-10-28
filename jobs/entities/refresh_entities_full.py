from db import SessionLocal
from services.entities import RefreshMode, refresh_entities


def run() -> dict:
    db = SessionLocal()
    try:
        return refresh_entities(
            db,
            mode=RefreshMode.ALL,
            symbol=None,
            create_if_missing=False,
            full_refresh=True,
        )
    finally:
        db.close()
