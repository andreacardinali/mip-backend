from pathlib import Path

from market_sources.discovery.candidate_resolver import (
    CandidateResolverConfig,
    resolve_discovery_candidates,
)


class FakeRepository:
    def __init__(self) -> None:
        self.snapshot_id = None
        self.retention_days = None

    def build_candidates(self, *, snapshot_id=None):
        self.snapshot_id = snapshot_id
        return {
            "snapshot_id": "snapshot-1",
            "total": 5,
            "resolved": 3,
            "core": 2,
            "discovery": 1,
            "ambiguous": 1,
            "unresolved": 1,
            "rejected": 0,
        }

    def delete_expired_candidates(self, retention_days):
        self.retention_days = retention_days
        return 4


def test_resolver_builds_and_cleans_candidates() -> None:
    repository = FakeRepository()

    result = resolve_discovery_candidates(
        repository,
        snapshot_id="snapshot-1",
        config=CandidateResolverConfig(
            retention_days=30,
        ),
    )

    assert result["status"] == "SUCCESS"
    assert result["core"] == 2
    assert result["discovery"] == 1
    assert result["expired_candidates_deleted"] == 4
    assert repository.snapshot_id == "snapshot-1"
    assert repository.retention_days == 30


def discovery_function(name):
    schema = (Path(__file__).resolve().parents[1] / "mip_schema.sql").read_text(
        encoding="utf-8"
    )
    return schema.split(f"CREATE FUNCTION public.{name}(", 1)[1].split("$$;", 1)[0]


def test_resolver_schema_builds_latest_snapshot_per_category():
    function = discovery_function("sp_market_discovery_build_candidates")
    assert "IF p_snapshot_id IS NULL THEN" in function
    assert "DISTINCT ON (source.source_type)" in function
    assert "WHERE source.is_enabled" in function
    assert "sp_market_discovery_build_candidates(v_snapshot_id)" in function
    assert "jsonb_array_elements(v_snapshot_results)" in function
    assert "('GAINERS', 'LOSERS', 'MOST_ACTIVE')" in function
    assert "THEN 100.0 ELSE 35.0 END" in function
    assert "'source_payload', scores.raw_payload" in function


def test_scan_schema_combines_categories_before_deduplication_and_limits():
    function = discovery_function("sp_market_discovery_list_scan_candidates")
    assert "DISTINCT ON (source.source_type)" in function
    assert "WHERE source.is_enabled" in function
    assert "WHERE p_snapshot_id IS NOT NULL" in function
    assert "WHERE p_snapshot_id IS NULL" in function
    assert function.index("deduplicated AS") < function.index("ROW_NUMBER()")
    assert function.index("ROW_NUMBER()") < function.index("limited AS")
    assert "FROM limited d" in function
