from datetime import UTC, datetime

import pytest

from market_sources.models import (
    DiscoveryCandidate,
    DiscoveryRequest,
    DiscoverySnapshot,
    DiscoverySourceType,
)
from services.market_discovery_refresh import (
    discovery_refresh_requests,
    run_market_discovery_refresh,
)


def test_requests_use_independent_category_settings():
    requests = discovery_refresh_requests(
        {
            "MARKET_DISCOVERY_REDDIT_TRENDING_ENABLED": "false",
            "MARKET_DISCOVERY_GAINERS_LIMIT": "15",
            "MARKET_DISCOVERY_LOSERS_ENABLED": "off",
        }
    )
    assert [(request.source_type, request.limit) for request in requests] == [
        (DiscoverySourceType.GAINERS, 15),
        (DiscoverySourceType.MOST_ACTIVE, 100),
    ]


@pytest.mark.parametrize("limit", [0, -1, 101])
def test_invalid_category_limit_is_rejected_before_refresh(limit):
    with pytest.raises(ValueError, match="GAINERS_LIMIT"):
        discovery_refresh_requests({"MARKET_DISCOVERY_GAINERS_LIMIT": limit})


@pytest.mark.parametrize("failed_type", [None, DiscoverySourceType.GAINERS])
def test_refresh_persists_each_category_and_isolates_failures(failed_type):
    requests = [
        DiscoveryRequest(source_type=source_type, limit=20)
        for source_type in (
            DiscoverySourceType.REDDIT_TRENDING,
            DiscoverySourceType.GAINERS,
            DiscoverySourceType.LOSERS,
            DiscoverySourceType.MOST_ACTIVE,
        )
    ]
    seen = []
    persisted = []

    class Service:
        def discover(self, request):
            seen.append(request)
            if request.source_type == failed_type:
                raise RuntimeError("provider unavailable")
            return DiscoverySnapshot(
                source_type=request.source_type,
                provider_name="fake",
                observed_at=datetime.now(UTC),
                candidates=(DiscoveryCandidate(symbol="AAPL", rank=1),),
            )

    def persist(snapshot):
        persisted.append(snapshot)
        return snapshot.source_type.value

    result = run_market_discovery_refresh(
        Service(), requests=requests, persist_snapshot=persist
    )

    assert seen == requests
    assert result["status"] == ("PARTIAL" if failed_type else "SUCCESS")
    assert result["snapshot_ids"] == [
        request.source_type.value
        for request in requests
        if request.source_type != failed_type
    ]
    assert result["items"] == len(persisted)
    assert len(result["failures"]) == (1 if failed_type else 0)


def test_refresh_disabled_does_not_call_providers():
    result = run_market_discovery_refresh(
        object(), requests=[], persist_snapshot=lambda snapshot: pytest.fail()
    )
    assert result["status"] == "SKIPPED"


def test_refresh_reports_persistence_failure_and_continues():
    class Service:
        def discover(self, request):
            return DiscoverySnapshot(
                source_type=request.source_type,
                provider_name="fake",
                observed_at=datetime.now(UTC),
                candidates=(),
            )

    def persist(snapshot):
        raise RuntimeError("transaction failed")

    result = run_market_discovery_refresh(
        Service(),
        requests=[DiscoveryRequest(source_type=DiscoverySourceType.GAINERS)],
        persist_snapshot=persist,
    )
    assert result["status"] == "FAILED"
    assert result["snapshot_ids"] == []
    assert result["failures"][0]["error"] == "transaction failed"
