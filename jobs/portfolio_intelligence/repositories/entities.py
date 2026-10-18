from sqlalchemy import text
from sqlalchemy.orm import Session
from jobs.portfolio_intelligence.models import EntityCandidate

def load_candidates(db: Session) -> list[EntityCandidate]:
    rows=db.execute(text("SELECT * FROM sp_get_intelligence_candidates()")).mappings().all()
    return [EntityCandidate(user_id=str(r["user_id"]), entity_id=str(r["entity_id"]),
        instrument_id=str(r["instrument_id"]), symbol=r["symbol"], name=r["name"],
        exchange=r["exchange"], currency=r["currency_code"], instrument_type=r["instrument_type"],
        quantity=float(r["quantity"] or 0), market_value=float(r["market_value"] or 0),
        allocation_pct=float(r["allocation_pct"] or 0), in_portfolio=bool(r["in_portfolio"]),
        in_watchlist=bool(r["in_watchlist"])) for r in rows]
