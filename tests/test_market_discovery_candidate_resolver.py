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
