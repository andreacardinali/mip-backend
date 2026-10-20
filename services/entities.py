import json
import logging
import time
from collections.abc import Callable
from enum import Enum
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from market_data.capabilities import InstrumentType
from market_data.factory import market_data
from market_data.models import InstrumentReference

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[dict[str, Any]], None]


class RefreshMode(str, Enum):
    MISSING = "MISSING"
    SYMBOL = "SYMBOL"
    ALL = "ALL"


def _emit(callback: ProgressCallback | None, event: str, **payload: Any) -> None:
    if callback:
        callback({"event": event, **payload})


def _row_value(row, name, default=None):
    mapping = getattr(row, "_mapping", {})
    return mapping.get(name, default)


def _instrument_type(value) -> InstrumentType:
    normalized = str(value or "").strip().upper()
    try:
        return InstrumentType(normalized)
    except ValueError:
        return InstrumentType.UNKNOWN


def _reference_from_row(row) -> InstrumentReference:
    return InstrumentReference(
        symbol=str(row.symbol),
        instrument_type=_instrument_type(_row_value(row, "instrument_type")),
        currency=_row_value(row, "currency_code"),
        exchange_code=_row_value(row, "exchange"),
    )


def _profile_payload(reference: InstrumentReference, *, full_refresh: bool):
    required_fields = {"name"}
    if full_refresh:
        required_fields.update({"currency", "exchange_code"})
    profile = market_data.get_profile(
        reference,
        required_fields=required_fields,
        include_quote=True,
    )
    return profile.to_import_payload() if profile else None


def _apply_reference_metadata(db, *, instrument_id, profile):
    db.execute(
        text(
            """
            SELECT sp_apply_instrument_reference_metadata(
                CAST(:instrument_id AS uuid),
                CAST(:payload AS jsonb)
            )
            """
        ),
        {
            "instrument_id": instrument_id,
            "payload": json.dumps(profile, default=str),
        },
    )


def refresh_entities(
    db: Session,
    *,
    mode: RefreshMode | str = RefreshMode.MISSING,
    symbol: str | None = None,
    create_if_missing: bool = False,
    full_refresh: bool = False,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    normalized_mode = _normalize_mode(mode)
    normalized_symbol = _normalize_symbol(symbol)
    if normalized_mode == RefreshMode.SYMBOL and not normalized_symbol:
        raise ValueError("symbol is required when mode is SYMBOL")

    started_at = time.monotonic()
    _emit(
        progress,
        "scan_started",
        mode=normalized_mode.value,
        symbol=normalized_symbol,
        create_if_missing=create_if_missing,
        full_refresh=full_refresh,
    )
    rows = db.execute(
        text("SELECT * FROM sp_get_entities_for_refresh(:mode, :symbol)"),
        {"mode": normalized_mode.value, "symbol": normalized_symbol},
    ).fetchall()
    total = len(rows)
    _emit(progress, "scan_loaded", total=total)

    if (
        normalized_mode == RefreshMode.SYMBOL
        and normalized_symbol
        and not rows
        and create_if_missing
    ):
        return _create_missing_entity(
            db, symbol=normalized_symbol, full_refresh=full_refresh, progress=progress
        )

    updated = 0
    skipped = 0
    errors = []
    for index, row in enumerate(rows, start=1):
        iteration_started_at = time.monotonic()
        row_symbol = str(row.symbol)
        _emit(
            progress,
            "entity_started",
            index=index,
            total=total,
            symbol=row_symbol,
            entity_id=str(row.entity_id),
            instrument_id=str(row.instrument_id),
        )
        try:
            _emit(
                progress,
                "provider_started",
                index=index,
                total=total,
                symbol=row_symbol,
            )
            reference = _reference_from_row(row)
            profile = _profile_payload(reference, full_refresh=full_refresh)
            if not profile:
                skipped += 1
                error = "Market-data provider returned no profile"
                errors.append({"symbol": row_symbol, "error": error})
                _emit(
                    progress,
                    "entity_skipped",
                    index=index,
                    total=total,
                    symbol=row_symbol,
                    reason=error,
                    duration_ms=round((time.monotonic() - iteration_started_at) * 1000),
                )
                continue

            _emit(
                progress,
                "profile_received",
                index=index,
                total=total,
                symbol=row_symbol,
                name=profile.get("name"),
                data_source=profile.get("data_source"),
                price_available=profile.get("last_price") is not None,
                exchange=profile.get("exchange"),
                currency=profile.get("currency"),
                instrument_type=profile.get("instrument_type"),
                identifiers=profile.get("identifiers") or {},
                price_source=profile.get("price_source"),
                price_timestamp=profile.get("price_timestamp"),
            )
            _emit(
                progress,
                "database_update_started",
                index=index,
                total=total,
                symbol=row_symbol,
            )
            with db.begin_nested():
                db.execute(
                    text(
                        "SELECT sp_refresh_entity_from_profile(:entity_id, :instrument_id, CAST(:payload AS jsonb))"
                    ),
                    {
                        "entity_id": row.entity_id,
                        "instrument_id": row.instrument_id,
                        "payload": json.dumps(profile, default=str),
                    },
                )
                _apply_reference_metadata(
                    db, instrument_id=row.instrument_id, profile=profile
                )

            updated += 1
            duration_ms = round((time.monotonic() - iteration_started_at) * 1000)
            _emit(
                progress,
                "entity_updated",
                index=index,
                total=total,
                symbol=row_symbol,
                duration_ms=duration_ms,
                updated=updated,
                skipped=skipped,
                failed=len(errors),
            )
            logger.info(
                "Entity refreshed symbol=%s entity_id=%s source=%s price_source=%s",
                row_symbol,
                row.entity_id,
                profile.get("data_source"),
                profile.get("price_source"),
            )
        except Exception as exc:
            error = str(exc)
            errors.append({"symbol": row_symbol, "error": error})
            _emit(
                progress,
                "entity_failed",
                index=index,
                total=total,
                symbol=row_symbol,
                error_type=type(exc).__name__,
                error=error,
                duration_ms=round((time.monotonic() - iteration_started_at) * 1000),
                updated=updated,
                skipped=skipped,
                failed=len(errors),
            )
            logger.exception(
                "Entity refresh failed symbol=%s entity_id=%s",
                row_symbol,
                row.entity_id,
            )

    result = {
        "status": _result_status(updated=updated, errors=errors),
        "mode": normalized_mode.value,
        "requested": total,
        "updated": updated,
        "created": 0,
        "skipped": skipped,
        "failed": len(errors),
        "errors": errors,
        "duration_ms": round((time.monotonic() - started_at) * 1000),
    }
    _emit(progress, "scan_completed", **result)
    return result


def _create_missing_entity(db, *, symbol, full_refresh, progress=None):
    started_at = time.monotonic()
    _emit(progress, "create_started", symbol=symbol)
    reference = InstrumentReference(symbol=symbol)
    profile = _profile_payload(reference, full_refresh=full_refresh)
    if not profile:
        result = {
            "status": "FAILED",
            "mode": RefreshMode.SYMBOL.value,
            "requested": 1,
            "updated": 0,
            "created": 0,
            "skipped": 0,
            "failed": 1,
            "errors": [{"symbol": symbol, "error": "Symbol not found"}],
            "duration_ms": round((time.monotonic() - started_at) * 1000),
        }
        _emit(progress, "create_failed", **result)
        return result

    _emit(
        progress,
        "profile_received",
        index=1,
        total=1,
        symbol=symbol,
        name=profile.get("name"),
        data_source=profile.get("data_source"),
        price_available=profile.get("last_price") is not None,
        exchange=profile.get("exchange"),
        currency=profile.get("currency"),
        instrument_type=profile.get("instrument_type"),
        identifiers=profile.get("identifiers") or {},
        price_source=profile.get("price_source"),
        price_timestamp=profile.get("price_timestamp"),
    )
    row = db.execute(
        text("SELECT * FROM sp_import_asset_profile(CAST(:payload AS jsonb))"),
        {"payload": json.dumps(profile, default=str)},
    ).first()
    if row is None:
        raise RuntimeError("sp_import_asset_profile returned no row")
    _apply_reference_metadata(db, instrument_id=row.instrument_id, profile=profile)
    result = {
        "status": "SUCCESS",
        "mode": RefreshMode.SYMBOL.value,
        "requested": 1,
        "updated": 0,
        "created": 1,
        "skipped": 0,
        "failed": 0,
        "errors": [],
        "entity_id": str(row.entity_id),
        "instrument_id": str(row.instrument_id),
        "duration_ms": round((time.monotonic() - started_at) * 1000),
    }
    _emit(progress, "create_completed", **result)
    return result


def _normalize_mode(mode):
    return (
        mode
        if isinstance(mode, RefreshMode)
        else RefreshMode(str(mode).strip().upper())
    )


def _normalize_symbol(symbol):
    if symbol is None:
        return None
    normalized = symbol.strip().upper()
    return normalized or None


def _result_status(*, updated, errors):
    if not errors:
        return "SUCCESS"
    return "PARTIAL" if updated > 0 else "FAILED"
