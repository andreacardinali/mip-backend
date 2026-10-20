import httpx
import pytest

from http_client.retry import RetryPolicy
from market_data.capabilities import InstrumentType
from market_data.exceptions import (
    CapabilityUnavailableError,
    ProviderTemporaryError,
)
from market_data.models import InstrumentReference
from market_data.providers.fmp import FMPProvider
from market_data.providers.ghostfolio import GhostfolioProvider


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fmp_retries_get_and_normalizes_profile():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(
            200,
            request=request,
            json=[
                {
                    "symbol": "LDO.MI",
                    "companyName": "Leonardo SpA",
                    "currency": "EUR",
                }
            ],
        )

    provider = FMPProvider(
        "secret",
        retry_policy=RetryPolicy(max_retries=1, jitter_seconds=0),
        client=_client(handler),
    )
    profile = provider.get_profile(
        InstrumentReference(symbol="LDO.MI", instrument_type=InstrumentType.STOCK)
    )
    assert profile.name == "Leonardo SpA"
    assert len(calls) == 2
    assert calls[-1].url.params["apikey"] == "secret"


def test_fmp_402_is_capability_unavailable():
    def handler(request):
        return httpx.Response(402, request=request)

    provider = FMPProvider("secret", client=_client(handler))
    with pytest.raises(CapabilityUnavailableError):
        provider.get_profile(InstrumentReference(symbol="LDO.MI"))


def test_ghostfolio_reauthenticates_once_after_401():
    calls = []

    def handler(request):
        calls.append(
            (request.method, request.url.path, request.headers.get("authorization"))
        )
        if request.url.path == "/api/v1/auth/anonymous":
            token = (
                "token-1"
                if sum(1 for x in calls if x[1].endswith("anonymous")) == 1
                else "token-2"
            )
            return httpx.Response(200, request=request, json={"authToken": token})
        if request.headers.get("authorization") == "Bearer token-1":
            return httpx.Response(401, request=request)
        return httpx.Response(
            200,
            request=request,
            json={
                "assetProfile": {
                    "symbol": "LDO.MI",
                    "name": "Leonardo SpA",
                    "currency": "EUR",
                },
                "marketData": [{"date": "2026-10-20T07:00:00Z", "marketPrice": 49.21}],
            },
        )

    provider = GhostfolioProvider(
        "http://ghostfolio.local",
        "security-token",
        client=_client(handler),
    )
    profile = provider.get_profile(InstrumentReference(symbol="LDO.MI"))
    assert profile.last_price == 49.21
    assert profile.price_timestamp is not None
    assert [call[1] for call in calls].count("/api/v1/auth/anonymous") == 2


def test_ghostfolio_refresh_post_is_not_retried():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/v1/auth/anonymous":
            return httpx.Response(200, request=request, json={"authToken": "token"})
        return httpx.Response(503, request=request)

    provider = GhostfolioProvider(
        "http://ghostfolio.local",
        "security-token",
        retry_policy=RetryPolicy(max_retries=3, jitter_seconds=0),
        client=_client(handler),
    )
    with pytest.raises(ProviderTemporaryError):
        provider.refresh_profile(InstrumentReference(symbol="LDO.MI"))
    refresh_calls = [call for call in calls if "gather/profile-data" in call[1]]
    assert len(refresh_calls) == 1
