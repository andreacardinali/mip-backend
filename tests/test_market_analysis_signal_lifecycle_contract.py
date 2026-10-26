from pathlib import Path


def test_lifecycle_contract():
    sql = Path("sql/20261026_market_analysis_signal_lifecycle.sql").read_text()
    for value in (
        "market_analysis_signal_events",
        "sp_market_analysis_reconcile_signal_lifecycle",
        "sp_market_analysis_close_missing_signals",
        "sp_market_analysis_list_signal_events",
        "DIRECTION_CHANGED",
        "CONFIRMED",
        "EXPIRED",
    ):
        assert value in sql
