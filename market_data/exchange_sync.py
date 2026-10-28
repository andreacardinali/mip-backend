"""Synchronize authoritative exchange metadata and holiday snapshots."""

import logging

from http_client import get_http_status
from market_time.holidays import normalize_exchange_code
from market_time.repository import SQLExchangeRepository

logger = logging.getLogger(__name__)


def acquire_holidays(provider, exchange_code, *, catalog=None):
    code = normalize_exchange_code(exchange_code)
    exchanges = provider.get_exchanges() if catalog is None else catalog
    matches = [item for item in exchanges if item.code == code]
    if len(matches) != 1:
        raise ValueError("Requested code must identify exactly one catalog exchange")
    return provider.get_exchange_holidays(code)


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


def _fetch_catalog(provider, *, strict=False):
    try:
        if strict:
            catalog = tuple(provider.get_exchanges(strict=True))
            if not catalog:
                raise ValueError(
                    "Empty Eulerpool exchange catalog cannot be synchronized"
                )
            return catalog
        return list(provider.get_exchanges())
    except Exception as exc:
        error_type = type(exc).__name__
        http_status = get_http_status(exc)
        detail = f"error_type={error_type}"
        if http_status is not None:
            detail += f" http_status={http_status}"
        logger.error(
            "Exchange catalog retrieval failed endpoint=/market/exchanges %s", detail
        )
        raise RuntimeError(
            f"Unable to retrieve Eulerpool exchange catalog ({detail})"
        ) from None


def sync_exchange_data(provider, session_factory):
    catalog = _fetch_catalog(provider, strict=True)
    with session_factory() as db, db.begin():
        repository = SQLExchangeRepository(db)
        catalog_upserted = repository.sync_catalog(catalog)
        targets = repository.list_exchanges("eulerpool")
    logger.info("Eulerpool exchange catalog synchronized upserted=%s", catalog_upserted)
    result = sync_holidays(provider, targets, session_factory, catalog=catalog)
    return {**result, "catalog_upserted": catalog_upserted}


def sync_holidays(provider, targets, session_factory, *, catalog=None):
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
    if catalog is None:
        catalog = _fetch_catalog(provider)
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
            snapshot = acquire_holidays(provider, code, catalog=catalog)
            with session_factory() as db, db.begin():
                snapshot_id = SQLExchangeRepository(db).save(exchange, snapshot)
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
            http_status = get_http_status(exc)
            failure = {
                "exchange": exchange,
                "exchange_code": code,
                "status": "FAILED",
                "error_type": error_type,
            }
            detail = f"error_type={error_type}"
            if http_status is not None:
                failure["http_status"] = http_status
                detail += f" http_status={http_status}"
            logger.error(
                "Holiday sync failed exchange=%s code=%s %s",
                exchange,
                code,
                detail,
            )
            results.append(failure)
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
