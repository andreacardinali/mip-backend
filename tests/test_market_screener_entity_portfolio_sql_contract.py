from pathlib import Path

SQL_PATH = Path("sql/20261026_market_screener_entity_portfolio.sql")


def test_entity_portfolio_sql_contract() -> None:
    sql = SQL_PATH.read_text(encoding="utf-8")
    assert "sp_market_screener_list_user_opportunities" in sql
    assert "sp_market_screener_get_user_opportunity" in sql
    assert "EXACT_INSTRUMENT" in sql
    assert "SAME_ISIN" in sql
    assert "SAME_ENTITY" in sql
    assert "portfolio.user_id = p_user_id" in sql
    assert "instrument_identity.is_primary DESC" in sql
