from pathlib import Path

API_SQL_PATH = Path("sql/20261026_market_screener_api.sql")
ENTITY_SQL_PATH = Path("sql/20261026_market_screener_entity_portfolio.sql")
ROUTER_PATH = Path("routers/market_screener.py")
REPOSITORY_PATH = Path("market_screener/api_repository.py")


def test_market_screener_api_uses_user_domain_stored_procedures() -> None:
    router = ROUTER_PATH.read_text(encoding="utf-8")
    repository = REPOSITORY_PATH.read_text(encoding="utf-8")
    api_sql = API_SQL_PATH.read_text(encoding="utf-8")
    entity_sql = ENTITY_SQL_PATH.read_text(encoding="utf-8")

    assert "sp_market_screener_list_user_opportunities" in repository
    assert "sp_market_screener_get_user_opportunity" in repository
    assert 'current_user["user_id"]' in router
    assert "user_id=current_user" in router

    assert "sp_market_screener_list_opportunities" in api_sql
    assert "sp_market_screener_get_opportunity" in api_sql
    assert "sp_market_screener_list_user_opportunities" in entity_sql
    assert "sp_market_screener_get_user_opportunity" in entity_sql


def test_market_screener_user_api_preserves_portfolio_isolation() -> None:
    repository = REPOSITORY_PATH.read_text(encoding="utf-8")
    entity_sql = ENTITY_SQL_PATH.read_text(encoding="utf-8")

    assert "CAST(:user_id AS uuid)" in repository
    assert "portfolio.user_id = p_user_id" in entity_sql
    assert "EXACT_INSTRUMENT" in entity_sql
    assert "SAME_ISIN" in entity_sql
    assert "SAME_ENTITY" in entity_sql
    assert "NO_MATCH" in entity_sql
