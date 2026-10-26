from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from market_sources.discovery.candidate_repository import (
    DiscoveryCandidateRepository,
)


@dataclass(frozen=True, slots=True)
class CandidateResolverConfig:
    retention_days: int = 90

    def __post_init__(self) -> None:
        if self.retention_days < 1:
            raise ValueError("retention_days must be positive")


def resolve_discovery_candidates(
    repository: DiscoveryCandidateRepository,
    *,
    snapshot_id: str | None = None,
    config: CandidateResolverConfig | None = None,
) -> dict[str, Any]:
    effective_config = config or CandidateResolverConfig()
    summary = repository.build_candidates(
        snapshot_id=snapshot_id,
    )
    deleted = repository.delete_expired_candidates(
        effective_config.retention_days,
    )

    return {
        "status": "SUCCESS",
        **summary,
        "expired_candidates_deleted": deleted,
    }
