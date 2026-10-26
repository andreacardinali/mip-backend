from pathlib import Path


def test_signal_visibility_sql_contract() -> None:
    sql = Path("sql/20261026_market_screener_signal_visibility.sql").read_text(
        encoding="utf-8"
    )
    for field in (
        "strategy_code",
        "signal_type",
        "signal_timeframe",
        "signal_trend",
        "signal_confidence",
        "reference_price",
        "invalidation_price",
        "target_price",
        "signal_reasons",
        "technical_evidence",
    ):
        assert field in sql
    assert "sp_market_screener_list_user_signals" in sql
    assert "trg_market_screener_populate_signal_fields" in sql
    assert "public.trading_signals" in sql
