from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from jobs.portfolio_intelligence.models import EntityCandidate


def load_acquisition_candidates(db: Session, branch: str) -> list[EntityCandidate]:
    rows = (
        db.execute(
            text("SELECT * FROM public.sp_get_intelligence_coverage(:branch)"),
            {"branch": branch},
        )
        .scalars()
        .all()
    )
    return [_candidate_from_row(row) for row in rows]


def load_candidates(db: Session, branch: str) -> list[EntityCandidate]:
    rows = (
        db.execute(
            text(
                """
                SELECT *
                FROM public.sp_get_collection_intelligence_candidates(:branch)
                """
            ),
            {"branch": branch},
        )
        .scalars()
        .all()
    )

    return [_candidate_from_row(row) for row in rows]


def _candidate_from_row(
    row: Mapping[str, Any],
) -> EntityCandidate:
    return EntityCandidate(
        user_id=str(row.get("user_id") or ""),
        entity_id=str(row["entity_id"]),
        instrument_id=str(row["instrument_id"]),
        symbol=str(row["symbol"]),
        name=str(row["name"]),
        exchange=row["exchange"],
        currency=row["currency_code"],
        instrument_type=row["instrument_type"],
        quantity=float(row.get("quantity") or 0),
        market_value=float(row.get("market_value") or 0),
        allocation_pct=float(row.get("allocation_pct") or 0),
        in_portfolio=bool(row.get("in_portfolio")),
        in_watchlist=bool(row.get("in_watchlist")),
        related_symbols=list(row["related_symbols"] or []),
        collection_type=row.get("collection_type"),
        collection_id=str(row["collection_id"]) if row.get("collection_id") else None,
        collection_name=row.get("collection_name"),
        exposure_available=row.get("allocation_pct") is not None,
        enabled_categories=list(row.get("enabled_categories") or []),
    )
