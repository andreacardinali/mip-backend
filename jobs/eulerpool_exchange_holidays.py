import logging

from market_time.repository import SQLHolidayRepository
from scripts.sync_exchange_holidays import acquire

logger = logging.getLogger(__name__)


def _resolve_target(target, catalog):
    canonical_codes = target.get("exchange_codes", [])
    if target.get("exchange_code"):
        canonical_codes = [*canonical_codes, target["exchange_code"]]
    mapped_identifiers = target.get("source_identifiers", [])
    identifiers = (
        canonical_codes
        or mapped_identifiers
        or [
            target["exchange"],
            target.get("exchangefullname"),
        ]
    )
    resolved_codes = set()
    for identifier in identifiers:
        if not identifier:
            continue
        needle = identifier.strip().casefold()
        matches = [item for item in catalog if item.code.casefold() == needle]
        if not matches and not canonical_codes:
            matches = [
                item
                for item in catalog
                if (item.name or "").strip().casefold() == needle
            ]
        if len(matches) > 1:
            return None, "ambiguous_exchange"
        if matches:
            resolved_codes.add(matches[0].code)
        elif canonical_codes:
            return None, "unresolved_exchange_mapping"
    if len(resolved_codes) > 1:
        return None, "ambiguous_exchange"
    if not resolved_codes:
        return None, (
            "unresolved_exchange_mapping"
            if mapped_identifiers
            else "unresolved_exchange"
        )
    return resolved_codes.pop(), None


def sync_holidays(provider, targets, session_factory):
    if not targets:
        logger.warning(
            "Holiday sync skipped reason=no_registered_exchanges eligible=0; "
            "no MIP exchange rows are available"
        )
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
    resolutions = [_resolve_target(target, catalog) for target in targets]
    code_counts = {}
    for code, reason in resolutions:
        if reason is None:
            code_counts[code] = code_counts.get(code, 0) + 1
    results = []
    for target, (code, reason) in zip(targets, resolutions, strict=True):
        exchange = target["exchange"]
        if reason is None and code_counts[code] > 1:
            reason = "conflicting_exchange_mapping"
        if reason:
            logger.warning(
                "Holiday sync skipped exchange=%s code=%s reason=%s",
                exchange,
                code,
                reason,
            )
            results.append(
                {
                    "exchange": exchange,
                    "exchange_code": code,
                    "status": "FAILED",
                    "reason": reason,
                }
            )
            continue
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
