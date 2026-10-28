from __future__ import annotations

from config.settings import settings
from job_runtime.database import run_transactional
from market_sources import create_market_source_service
from market_sources.discovery import DiscoveryRepository
from services.market_discovery_refresh import (
    discovery_refresh_requests,
    run_market_discovery_refresh,
)


def run() -> dict[str, object]:
    requests = discovery_refresh_requests(settings)
    if not requests:
        return {"status": "SKIPPED", "reason": "disabled", "snapshots": []}
    retention_days = int(settings.get("MARKET_DISCOVERY_RETENTION_DAYS", 90))
    if retention_days < 1:
        raise ValueError("MARKET_DISCOVERY_RETENTION_DAYS must be positive")

    result = run_market_discovery_refresh(
        create_market_source_service(),
        requests=requests,
        persist_snapshot=lambda snapshot: run_transactional(
            lambda db: str(DiscoveryRepository(db).save_snapshot(snapshot))
        ),
    )
    result["expired_snapshots_deleted"] = run_transactional(
        lambda db: DiscoveryRepository(db).delete_expired_snapshots(retention_days)
    )
    return result


if __name__ == "__main__":
    print(run())
