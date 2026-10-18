import json
import logging
from enum import Enum
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from providers.manager import market_data


logger = logging.getLogger(__name__)


class RefreshMode(str, Enum):
    MISSING = "MISSING"
    SYMBOL = "SYMBOL"
    ALL = "ALL"


def refresh_entities(
    db: Session,
    *,
    mode: RefreshMode | str = RefreshMode.MISSING,
    symbol: str | None = None,
    create_if_missing: bool = False,
    full_refresh: bool = False,
) -> dict[str, Any]:
    normalized_mode = _normalize_mode(mode)
    normalized_symbol = _normalize_symbol(symbol)

    if normalized_mode == RefreshMode.SYMBOL and not normalized_symbol:
        raise ValueError("symbol is required when mode is SYMBOL")

    rows = db.execute(
        text(
            """
            SELECT *
            FROM sp_get_entities_for_refresh(
                :mode,
                :symbol
            )
            """
        ),
        {
            "mode": normalized_mode.value,
            "symbol": normalized_symbol,
        },
    ).fetchall()

    if (
        normalized_mode == RefreshMode.SYMBOL
        and normalized_symbol
        and not rows
        and create_if_missing
    ):
        return _create_missing_entity(
            db,
            symbol=normalized_symbol,
            full_refresh=full_refresh,
        )

    updated = 0
    skipped = 0
    errors: list[dict[str, str]] = []

    for row in rows:
        row_symbol = str(row.symbol)

        try:
            profile = market_data.get_profile(
                row_symbol,
                full_refresh=full_refresh,
            )

            if not profile:
                skipped += 1
                errors.append(
                    {
                        "symbol": row_symbol,
                        "error": "Market-data provider returned no profile",
                    }
                )
                continue

            with db.begin_nested():
                db.execute(
                    text(
                        """
                        SELECT sp_refresh_entity_from_profile(
                            :entity_id,
                            :instrument_id,
                            CAST(:payload AS jsonb)
                        )
                        """
                    ),
                    {
                        "entity_id": row.entity_id,
                        "instrument_id": row.instrument_id,
                        "payload": json.dumps(profile, default=str),
                    },
                )

            updated += 1

            logger.info(
                "Entity refreshed symbol=%s entity_id=%s source=%s",
                row_symbol,
                row.entity_id,
                profile.get("data_source"),
            )

        except Exception as exc:
            errors.append(
                {
                    "symbol": row_symbol,
                    "error": str(exc),
                }
            )
            logger.exception(
                "Entity refresh failed symbol=%s entity_id=%s",
                row_symbol,
                row.entity_id,
            )

    return {
        "status": _result_status(updated=updated, errors=errors),
        "mode": normalized_mode.value,
        "requested": len(rows),
        "updated": updated,
        "created": 0,
        "skipped": skipped,
        "failed": len(errors),
        "errors": errors,
    }


def _create_missing_entity(
    db: Session,
    *,
    symbol: str,
    full_refresh: bool,
) -> dict[str, Any]:
    profile = market_data.get_profile(
        symbol,
        full_refresh=full_refresh,
    )

    if not profile:
        return {
            "status": "FAILED",
            "mode": RefreshMode.SYMBOL.value,
            "requested": 1,
            "updated": 0,
            "created": 0,
            "skipped": 0,
            "failed": 1,
            "errors": [
                {
                    "symbol": symbol,
                    "error": "Symbol not found",
                }
            ],
        }

    row = db.execute(
        text(
            """
            SELECT *
            FROM sp_import_asset_profile(
                CAST(:payload AS jsonb)
            )
            """
        ),
        {
            "payload": json.dumps(profile, default=str),
        },
    ).first()

    if row is None:
        raise RuntimeError("sp_import_asset_profile returned no row")

    return {
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
    }


def _normalize_mode(mode: RefreshMode | str) -> RefreshMode:
    if isinstance(mode, RefreshMode):
        return mode
    return RefreshMode(str(mode).strip().upper())


def _normalize_symbol(symbol: str | None) -> str | None:
    if symbol is None:
        return None
    normalized = symbol.strip().upper()
    return normalized or None


def _result_status(
    *,
    updated: int,
    errors: list[dict[str, str]],
) -> str:
    if not errors:
        return "SUCCESS"
    if updated > 0:
        return "PARTIAL"
    return "FAILED"
