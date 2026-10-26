from pathlib import Path


def test_scan_repository_uses_only_stored_procedure() -> None:
    source = Path("market_sources/discovery/scan_repository.py").read_text(
        encoding="utf-8"
    )

    assert "sp_market_discovery_list_scan_candidates" in source
    assert "FROM public.market_" not in source
    assert "INSERT INTO" not in source


def test_scan_candidate_sp_uses_domain_action_object_name() -> None:
    sql = Path("sql/20261026_market_discovery_scan_candidates.sql").read_text(
        encoding="utf-8"
    )

    assert "sp_market_discovery_list_scan_candidates" in sql
