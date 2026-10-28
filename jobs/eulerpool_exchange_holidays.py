import logging

from market_time.repository import SQLHolidayRepository
from scripts.sync_exchange_holidays import acquire

logger = logging.getLogger(__name__)


def sync_holidays(provider, targets, session_factory):
    if not targets:
        return {
            "status": "INCOMPLETE",
            "reason": "no_registered_exchanges",
            "eligible": 0,
            "upserted": 0,
            "failed": 0,
            "exchanges": [],
        }
    try:
        catalog = list(provider.get_exchanges())
    except Exception:
        raise RuntimeError("Unable to retrieve Eulerpool exchange catalog") from None
    results = []
    for target in targets:
        exchange = target["exchange"]
        code = target["exchange_code"]
        try:
            snapshot = acquire(provider, code, catalog=catalog)
            with session_factory() as db, db.begin():
                snapshot_id = SQLHolidayRepository(db).save(exchange, snapshot)
            results.append(
                {
                    "exchange": exchange,
                    "exchange_code": code,
                    "status": "SUCCESS",
                    "snapshot_id": str(snapshot_id),
                }
            )
            logger.info("Holiday snapshot stored exchange=%s code=%s", exchange, code)
        except Exception as exc:
            error_type = type(exc).__name__
            logger.error(
                "Holiday sync failed exchange=%s code=%s error_type=%s",
                exchange,
                code,
                error_type,
            )
            results.append(
                {
                    "exchange": exchange,
                    "exchange_code": code,
                    "status": "FAILED",
                    "error_type": error_type,
                }
            )
    failed = sum(result["status"] == "FAILED" for result in results)
    return {
        "status": (
            "FAILED" if failed == len(results) else "PARTIAL" if failed else "SUCCESS"
        ),
        "eligible": len(results),
        "upserted": len(results) - failed,
        "failed": failed,
        "exchanges": results,
    }


def run():
    from config.settings import settings
    from db import SessionLocal
    from market_data.providers.eulerpool import EulerpoolProvider

    key = settings.get("EULERPOOL_API_KEY")
    if not key:
        raise ValueError("EULERPOOL_API_KEY is required in backend configuration")
    with SessionLocal() as db:
        targets = SQLHolidayRepository(db).list_exchanges("eulerpool")
    return sync_holidays(EulerpoolProvider(key), targets, SessionLocal)
