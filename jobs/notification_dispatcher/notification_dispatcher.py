from db import SessionLocal
from notifications.config import NotificationConfig
from notifications.dispatcher import NotificationDispatcher


def run() -> dict:
    db = SessionLocal()
    try:
        config = NotificationConfig.load()
        return NotificationDispatcher(db, config).run_once()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    print(run())
