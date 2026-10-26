from __future__ import annotations

import os

from db import SessionLocal
from market_sources import create_market_source_service
from market_sources.discovery import DiscoveryRepository
from market_sources.models import DiscoveryRequest, DiscoverySourceType


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def run() -> dict[str, object]:
    enabled = _env_bool("MARKET_DISCOVERY_REDDIT_TRENDING_ENABLED", True)
    if not enabled:
        return {"status": "SKIPPED", "reason": "disabled"}
    limit = int(os.getenv("MARKET_DISCOVERY_REDDIT_TRENDING_LIMIT", "100"))
    retention_days = int(os.getenv("MARKET_DISCOVERY_RETENTION_DAYS", "90"))
    if not 1 <= limit <= 100:
        raise ValueError(
            "MARKET_DISCOVERY_REDDIT_TRENDING_LIMIT must be between 1 and 100"
        )
    if retention_days < 1:
        raise ValueError("MARKET_DISCOVERY_RETENTION_DAYS must be positive")

    result = create_market_source_service().discover(
        DiscoveryRequest(source_type=DiscoverySourceType.REDDIT_TRENDING, limit=limit)
    )
    if len(result.candidates) != result.returned_count:
        raise ValueError("Discovery result is partial or internally inconsistent")

    db = SessionLocal()
    try:
        repository = DiscoveryRepository(db)
        snapshot_id = repository.save_snapshot(result)
        deleted = repository.delete_expired_snapshots(retention_days)
        db.commit()
        return {
            "status": "SUCCESS",
            "snapshot_id": str(snapshot_id),
            "provider": result.provider,
            "source_type": result.source_type.value,
            "items": result.returned_count,
            "expired_snapshots_deleted": deleted,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    print(run())
