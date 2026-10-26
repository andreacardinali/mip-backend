from pathlib import Path


def test_sql_contract():
    s = Path("sql/20261026_market_screener_strategy_consensus.sql").read_text()
    for x in (
        "sp_market_screener_apply_consensus",
        "sp_market_screener_enrich_consensus_payload",
        "consensus_direction",
        "supporting_strategies",
        "trg_market_screener_snapshot_consensus",
    ):
        assert x in s
