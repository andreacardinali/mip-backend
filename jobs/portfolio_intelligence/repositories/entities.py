from sqlalchemy import text
from sqlalchemy.orm import Session
from jobs.portfolio_intelligence.models import EntityCandidate

SQL = text("""
WITH portfolio_rows AS (
    SELECT pf.user_id, i.entity_id, i.instrument_id, i.symbol, e.name,
           i.exchange, i.currency_code, i.instrument_type,
           SUM(p.quantity)::numeric AS quantity,
           SUM(COALESCE(p.quantity * i.last_price, 0))::numeric AS market_value
    FROM positions p
    JOIN portfolios pf ON pf.portfolio_id = p.portfolio_id
    JOIN instruments i ON i.instrument_id = p.instrument_id
    JOIN entities e ON e.entity_id = i.entity_id
    JOIN users u ON u.user_id = pf.user_id AND u.is_active = TRUE
    WHERE e.active = TRUE AND i.symbol IS NOT NULL
    GROUP BY pf.user_id, i.entity_id, i.instrument_id, i.symbol, e.name,
             i.exchange, i.currency_code, i.instrument_type
),
watchlist_rows AS (
    SELECT DISTINCT w.user_id, i.entity_id, i.instrument_id, i.symbol, e.name,
           i.exchange, i.currency_code, i.instrument_type
    FROM watchlist_items wi
    JOIN watchlists w ON w.watchlist_id = wi.watchlist_id
    JOIN users u ON u.user_id = w.user_id AND u.is_active = TRUE
    JOIN entities e ON e.entity_id = wi.entity_id
    JOIN instruments i ON i.entity_id = e.entity_id
    WHERE e.active = TRUE AND i.symbol IS NOT NULL
),
combined AS (
    SELECT user_id, entity_id, instrument_id, symbol, name, exchange,
           currency_code, instrument_type, quantity, market_value,
           TRUE AS in_portfolio, FALSE AS in_watchlist
    FROM portfolio_rows
    UNION ALL
    SELECT user_id, entity_id, instrument_id, symbol, name, exchange,
           currency_code, instrument_type, 0::numeric, 0::numeric,
           FALSE, TRUE
    FROM watchlist_rows
),
aggregated AS (
    SELECT user_id, entity_id, instrument_id, symbol, name, exchange,
           currency_code, instrument_type,
           SUM(quantity)::numeric AS quantity,
           SUM(market_value)::numeric AS market_value,
           BOOL_OR(in_portfolio) AS in_portfolio,
           BOOL_OR(in_watchlist) AS in_watchlist
    FROM combined
    GROUP BY user_id, entity_id, instrument_id, symbol, name, exchange,
             currency_code, instrument_type
),
totals AS (
    SELECT user_id, NULLIF(SUM(market_value), 0) AS total_value
    FROM aggregated GROUP BY user_id
)
SELECT a.*,
       CASE WHEN t.total_value IS NULL THEN 0
            ELSE ROUND(a.market_value / t.total_value * 100, 4)
       END AS allocation_pct
FROM aggregated a
JOIN totals t ON t.user_id = a.user_id
ORDER BY a.user_id, a.symbol, a.exchange NULLS LAST
""")

def load_candidates(db: Session) -> list[EntityCandidate]:
    rows=db.execute(SQL).mappings().all()
    return [EntityCandidate(
        user_id=str(r["user_id"]), entity_id=str(r["entity_id"]),
        instrument_id=str(r["instrument_id"]), symbol=r["symbol"],
        name=r["name"], exchange=r["exchange"], currency=r["currency_code"],
        instrument_type=r["instrument_type"], quantity=float(r["quantity"] or 0),
        market_value=float(r["market_value"] or 0),
        allocation_pct=float(r["allocation_pct"] or 0),
        in_portfolio=bool(r["in_portfolio"]), in_watchlist=bool(r["in_watchlist"])
    ) for r in rows]
