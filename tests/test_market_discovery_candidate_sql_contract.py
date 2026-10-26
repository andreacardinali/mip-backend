from pathlib import Path

EXPECTED_PROCEDURES = {
    "sp_market_discovery_build_candidates",
    "sp_market_discovery_list_candidates",
    "sp_market_discovery_get_candidate",
    "sp_market_discovery_delete_expired_candidates",
}


def test_candidate_sql_uses_established_sp_naming() -> None:
    sql = Path("sql/20261026_market_discovery_candidate_resolver.sql").read_text(
        encoding="utf-8"
    )

    for procedure in EXPECTED_PROCEDURES:
        assert procedure in sql


def test_candidate_repository_calls_only_stored_procedures() -> None:
    source = Path("market_sources/discovery/candidate_repository.py").read_text(
        encoding="utf-8"
    )

    assert "FROM public.market_" not in source
    assert "INSERT INTO" not in source
    assert "UPDATE " not in source
    assert "DELETE FROM" not in source
    assert "sp_market_discovery_" in source
