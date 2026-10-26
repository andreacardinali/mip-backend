from pathlib import Path


def test_pipeline_and_snapshot_contract() -> None:
    worker = Path("jobs/market_screener_pipeline/worker.py").read_text()
    sql = Path("sql/20261026_market_screener_runs_snapshots.sql").read_text()
    router = Path("routers/market_screener.py").read_text()
    for phase in (
        "run_discovery_refresh",
        "run_candidate_resolver",
        "run_analysis_scan",
        "run_llm_enrichment",
        "run_ranking",
    ):
        assert phase in worker
    for routine in (
        "sp_market_screener_start_run",
        "sp_market_screener_complete_run",
        "sp_market_screener_create_ranking_snapshot",
        "sp_market_screener_list_runs",
        "sp_market_screener_list_ranking_snapshots",
        "sp_market_screener_list_snapshot_items",
    ):
        assert routine in sql
    assert '@router.get("/runs")' in router
    assert '@router.get("/snapshots")' in router
