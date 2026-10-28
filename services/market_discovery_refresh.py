from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from market_sources.models import (
    DiscoveryRequest,
    DiscoverySnapshot,
    DiscoverySourceType,
)


def discovery_refresh_requests(settings: Mapping[str, Any]) -> list[DiscoveryRequest]:
    requests = []
    for source_type in (
        DiscoverySourceType.REDDIT_TRENDING,
        DiscoverySourceType.GAINERS,
        DiscoverySourceType.LOSERS,
        DiscoverySourceType.MOST_ACTIVE,
    ):
        prefix = f"MARKET_DISCOVERY_{source_type.value}"
        if str(settings.get(f"{prefix}_ENABLED", True)).strip().lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            continue
        limit = int(settings.get(f"{prefix}_LIMIT", 100))
        if not 1 <= limit <= 100:
            raise ValueError(f"{prefix}_LIMIT must be between 1 and 100")
        requests.append(DiscoveryRequest(source_type=source_type, limit=limit))
    return requests


def run_market_discovery_refresh(
    service: Any,
    *,
    requests: Sequence[DiscoveryRequest],
    persist_snapshot: Callable[[DiscoverySnapshot], str],
) -> dict[str, Any]:
    if not requests:
        return {"status": "SKIPPED", "reason": "disabled", "snapshots": []}

    snapshots = []
    failures = []
    for request in requests:
        try:
            snapshot = service.discover(request)
            if snapshot.source_type != request.source_type:
                raise ValueError("Discovery result has an unexpected source type")
            if len(snapshot.candidates) != snapshot.returned_count:
                raise ValueError("Discovery result is internally inconsistent")
            snapshot_id = persist_snapshot(snapshot)
            snapshots.append(
                {
                    "snapshot_id": str(snapshot_id),
                    "provider": snapshot.provider,
                    "source_type": snapshot.source_type.value,
                    "items": snapshot.returned_count,
                }
            )
        except Exception as exc:
            failures.append(
                {"source_type": request.source_type.value, "error": str(exc)}
            )

    return {
        "status": (
            "PARTIAL"
            if snapshots and failures
            else ("FAILED" if failures else "SUCCESS")
        ),
        "snapshot_id": snapshots[0]["snapshot_id"] if snapshots else None,
        "provider": snapshots[0]["provider"] if snapshots else None,
        "source_type": snapshots[0]["source_type"] if snapshots else None,
        "snapshot_ids": [snapshot["snapshot_id"] for snapshot in snapshots],
        "snapshots": snapshots,
        "items": sum(snapshot["items"] for snapshot in snapshots),
        "failures": failures,
    }
