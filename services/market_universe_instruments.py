from __future__ import annotations

import logging
from typing import Any

from market_data.capabilities import InstrumentType
from services.entities import RefreshMode, refresh_entities

logger = logging.getLogger(__name__)


def import_universe_instrument(db, symbol: str) -> bool:
    """Create a missing index constituent as a stock without loading a quote."""
    normalized_symbol = str(symbol or "").strip().upper()
    if not normalized_symbol:
        return False

    try:
        with db.begin_nested():
            result: dict[str, Any] = refresh_entities(
                db,
                mode=RefreshMode.SYMBOL,
                symbol=normalized_symbol,
                create_if_missing=True,
                full_refresh=False,
                instrument_type=InstrumentType.STOCK,
                include_quote=False,
            )
    except Exception:
        logger.exception(
            "Market universe instrument import failed symbol=%s",
            normalized_symbol,
        )
        return False

    succeeded = (
        result.get("status") == "SUCCESS"
        and int(result.get("failed", 0)) == 0
        and (
            result.get("instrument_id") is not None
            or int(result.get("created", 0)) > 0
            or int(result.get("updated", 0)) > 0
        )
    )
    if not succeeded:
        logger.warning(
            "Market universe instrument import returned no persisted instrument "
            "symbol=%s result=%s",
            normalized_symbol,
            result,
        )
    return succeeded
