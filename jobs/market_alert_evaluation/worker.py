from __future__ import annotations

import logging
import os

from alerts.service import evaluate_and_enqueue_market_alerts
from db import SessionLocal

logger = logging.getLogger(__name__)


def _enabled(name: str, default: bool = True) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def run() -> dict[str, object]:
    db = SessionLocal()
    try:
        result = evaluate_and_enqueue_market_alerts(
            db,
            algorithm_version=os.getenv("MARKET_SCREENER_SIGNAL_ALGORITHM_VERSION")
            or None,
            max_candidates=max(1, int(os.getenv("MARKET_ALERT_MAX_CANDIDATES", "500"))),
            notifications_enabled=_enabled("NOTIFICATIONS_ENABLED", True),
        )
        db.commit()
        logger.info("Market alert evaluation completed summary=%s", result)
        return result
    except Exception:
        db.rollback()
        logger.exception("Market alert evaluation failed")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run())
