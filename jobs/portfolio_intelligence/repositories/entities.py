from sqlalchemy import text
from sqlalchemy.orm import Session

from jobs.portfolio_intelligence.models import (
    EntityCandidate,
)


def load_candidates(
    db: Session,
) -> list[EntityCandidate\]:
    rows = db.execute(
        text(
            "SELECT * "
            "FROM sp_get_intelligence_candidates()"
        )
    ).mappings().all()

    return [
        EntityCandidate(
            user_id=str(row["user_id"]),
            entity_id=str(row["entity_id"]),
            instrument_id=str(
                row["instrument_id"]
            ),
            symbol=row["symbol"],
            name=row["name"],
            exchange=row["exchange"],
            currency=row["currency_code"],
            instrument_type=(
                row["instrument_type"]
            ),
            quantity=float(
                row["quantity"] or 0
            ),
            market_value=float(
                row["market_value"] or 0
            ),
            allocation_pct=float(
                row["allocation_pct"] or 0
            ),
            in_portfolio=bool(
                row["in_portfolio"]
            ),
            in_watchlist=bool(
                row["in_watchlist"]
            ),
        )
        for row in rows
    ]