from datetime import UTC, datetime

from market_sources.models import (
    DiscoveryCandidate,
    DiscoverySnapshot,
    DiscoverySourceType,
)


def test_snapshot_candidate_has_apewisdom_normalized_fields() -> None:
    candidate = DiscoveryCandidate(
        symbol="AAPL",
        name="Apple",
        rank=1,
        previous_rank=3,
        mentions=42,
        previous_mentions=21,
        upvotes=999,
        source_payload={"ticker": "AAPL"},
    )

    snapshot = DiscoverySnapshot(
        provider_name="apewisdom",
        source_type=DiscoverySourceType.REDDIT_TRENDING,
        observed_at=datetime.now(UTC),
        requested_limit=100,
        candidates=(candidate,),
        source_reference="all-stocks",
        source_metadata={"page": 1},
    )

    assert snapshot.provider_name == "apewisdom"
    assert snapshot.provider == "apewisdom"
    assert snapshot.returned_count == 1
    assert snapshot.effective_requested_limit == 100
    assert snapshot.candidates[0].previous_mentions == 21
    assert snapshot.candidates[0].upvotes == 999
