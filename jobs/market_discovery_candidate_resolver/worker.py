from __future__ import annotations

import os

from job_runtime.database import run_transactional
from market_sources.discovery import (
    CandidateResolverConfig,
    DiscoveryCandidateRepository,
    resolve_discovery_candidates,
)


def run() -> dict[str, object]:
    retention_days = int(
        os.getenv(
            "MARKET_DISCOVERY_CANDIDATE_RETENTION_DAYS",
            "90",
        )
    )

    return run_transactional(
        lambda db: resolve_discovery_candidates(
            DiscoveryCandidateRepository(db),
            config=CandidateResolverConfig(
                retention_days=retention_days,
            ),
        )
    )


if __name__ == "__main__":
    print(run())
