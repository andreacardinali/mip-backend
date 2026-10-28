from __future__ import annotations

from datetime import UTC, datetime

import pytest

from http_client import testing as httpx
from http_client.retry import RetryPolicy
from market_sources.discovery.providers import apewisdom as module
from market_sources.discovery.providers.apewisdom import (
    APEWISDOM_ALL_STOCKS_URL,
    ApeWisdomDiscoveryProvider,
    normalize_apewisdom_symbol,
    parse_apewisdom_candidates,
)
from market_sources.exceptions import (
    InvalidSourceResponseError,
    SourceRateLimitError,
)
from market_sources.models import (
    DiscoveryRequest,
    DiscoverySourceType,
)


def _response(
    status_code: int,
    payload: object,
) -> httpx.Response:
    request = httpx.Request(
        "GET",
        APEWISDOM_ALL_STOCKS_URL,
    )

    return httpx.Response(
        status_code,
        request=request,
        json=payload,
    )


def _payload() -> dict[str, object]:
    return {
        "count": 3,
        "pages": 1,
        "current_page": 1,
        "results": [
            {
                "rank": "1",
                "ticker": "AAPL",
                "name": "Apple Inc.",
                "mentions": "42",
                "upvotes": "999",
                "rank_24h_ago": "0",
                "mentions_24h_ago": "0",
            },
            {
                "rank": "2",
                "ticker": "NVDA",
                "name": "NVIDIA Corporation",
                "mentions": "30",
                "upvotes": "500",
                "rank_24h_ago": "5",
                "mentions_24h_ago": "12",
            },
            {
                "rank": "3",
                "ticker": "AAPL",
                "name": "Duplicate Apple",
                "mentions": "1",
                "upvotes": "1",
                "rank_24h_ago": "2",
                "mentions_24h_ago": "1",
            },
        ],
    }


def test_normalize_apewisdom_symbol() -> None:
    assert normalize_apewisdom_symbol(" aapl ") == "AAPL"
    assert normalize_apewisdom_symbol("BRK.B") is None
    assert normalize_apewisdom_symbol("123") is None
    assert normalize_apewisdom_symbol(None) is None


def test_parse_candidates_normalizes_apewisdom_fields() -> None:
    candidates = parse_apewisdom_candidates(
        _payload(),
        limit=100,
    )

    assert len(candidates) == 2

    apple = candidates[0]
    assert apple.symbol == "AAPL"
    assert apple.name == "Apple Inc."
    assert apple.rank == 1
    assert apple.previous_rank is None
    assert apple.mentions == 42
    assert apple.previous_mentions == 0
    assert apple.upvotes == 999
    assert apple.source_payload["ticker"] == "AAPL"

    nvidia = candidates[1]
    assert nvidia.symbol == "NVDA"
    assert nvidia.name == "NVIDIA Corporation"
    assert nvidia.rank == 2
    assert nvidia.previous_rank == 5
    assert nvidia.mentions == 30
    assert nvidia.previous_mentions == 12
    assert nvidia.upvotes == 500


def test_parse_candidates_uses_source_position_for_invalid_rank() -> None:
    payload = {
        "results": [
            {
                "rank": "0",
                "ticker": "AAPL",
                "name": "Apple Inc.",
                "mentions": "1",
                "upvotes": "0",
                "rank_24h_ago": "-1",
                "mentions_24h_ago": "0",
            }
        ]
    }

    candidate = parse_apewisdom_candidates(
        payload,
        limit=10,
    )[0]

    assert candidate.rank == 1
    assert candidate.previous_rank is None


def test_parse_candidates_honors_limit() -> None:
    candidates = parse_apewisdom_candidates(
        _payload(),
        limit=1,
    )

    assert [item.symbol for item in candidates] == ["AAPL"]


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"results": None},
        {"results": {}},
    ],
)
def test_parse_candidates_rejects_invalid_payload(
    payload: object,
) -> None:
    with pytest.raises(InvalidSourceResponseError):
        parse_apewisdom_candidates(
            payload,
            limit=10,
        )


def test_provider_uses_shared_retry_and_builds_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    retry_policy = RetryPolicy(max_retries=2)

    def fake_request_with_retry(
        client: httpx.Client,
        method: str,
        url: str,
        **kwargs: object,
    ) -> httpx.Response:
        captured.update(
            {
                "client": client,
                "method": method,
                "url": url,
                **kwargs,
            }
        )
        return _response(200, _payload())

    monkeypatch.setattr(
        module,
        "request_with_retry",
        fake_request_with_retry,
    )

    client = httpx.Client()
    try:
        provider = ApeWisdomDiscoveryProvider(
            user_agent="MIP/1.0",
            retry_policy=retry_policy,
            client=client,
        )

        snapshot = provider.discover(
            DiscoveryRequest(
                source_type=(DiscoverySourceType.REDDIT_TRENDING),
                limit=25,
            )
        )
    finally:
        client.close()

    assert captured["method"] == "GET"
    assert captured["url"] == APEWISDOM_ALL_STOCKS_URL
    assert captured["policy"] is retry_policy
    assert captured["headers"] == {
        "User-Agent": "MIP/1.0",
        "Accept": "application/json",
    }

    assert snapshot.provider_name == "apewisdom"
    assert snapshot.provider == "apewisdom"
    assert snapshot.source_type == (DiscoverySourceType.REDDIT_TRENDING)
    assert snapshot.requested_limit == 25
    assert snapshot.returned_count == 2
    assert snapshot.source_reference == (APEWISDOM_ALL_STOCKS_URL)
    assert snapshot.observed_at.tzinfo is not None
    assert snapshot.observed_at <= datetime.now(UTC)


def test_provider_translates_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_request_with_retry(
        *_args: object,
        **_kwargs: object,
    ) -> httpx.Response:
        response = _response(
            429,
            {"error": "rate limited"},
        )
        raise httpx.HTTPStatusError(
            "rate limited",
            request=response.request,
            response=response,
        )

    monkeypatch.setattr(
        module,
        "request_with_retry",
        fake_request_with_retry,
    )

    provider = ApeWisdomDiscoveryProvider(
        user_agent="MIP/1.0",
        client=httpx.Client(),
    )

    try:
        with pytest.raises(SourceRateLimitError):
            provider.discover(
                DiscoveryRequest(
                    source_type=(DiscoverySourceType.REDDIT_TRENDING),
                    limit=25,
                )
            )
    finally:
        provider._client.close()


def test_provider_rejects_unsupported_discovery_source() -> None:
    provider = ApeWisdomDiscoveryProvider(
        user_agent="MIP/1.0",
        client=httpx.Client(),
    )

    try:
        with pytest.raises(InvalidSourceResponseError):
            provider.discover(
                DiscoveryRequest(
                    source_type=DiscoverySourceType.GAINERS,
                    limit=25,
                )
            )
    finally:
        provider._client.close()
