from pathlib import Path


def test_market_screener_sp_naming_and_no_application_sql():
    sql = Path("sql/20261026_market_screener_ranking.sql").read_text()
    assert "sp_market_screener_list_ranking_inputs" in sql
    assert "sp_market_screener_replace_ranking" in sql
