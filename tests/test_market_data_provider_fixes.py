from types import SimpleNamespace

import pytest

from http_client import RetryPolicy, get_http_status
from http_client import testing as httpx
from market_data.capabilities import InstrumentType
from market_data.exceptions import CapabilityUnavailableError, ProviderRateLimitError
from market_data.models import InstrumentReference
from market_data.providers import fmp
from market_data.providers.eulerpool import EulerpoolProvider
from market_data.providers.fmp import FMPProvider, FMPQuotaGuard, _normalize_http_error


class FakeEulerpoolProvider(EulerpoolProvider):
    def __init__(self):
        self.api_key = "test"
        self.base_url = "test"
        self.timeout = 1
        self.retry_policy = None
        self._client = None
        self._exchange_cache = None
        self.calls = []

    def _get(self, path, params=None):
        self.calls.append(path)
        if path.startswith("/equity/profile/"):
            return {
                "isin": "IT0003856405",
                "symbol": "LDO.MI",
                "name": "Leonardo SpA",
                "currency": "EUR",
            }
        if path.startswith("/market/quotes/exchanges/"):
            return {"primary": "Milan"}
        if path == "/market/exchanges":
            return [
                {
                    "code": "XMIL",
                    "name": "Borsa Italiana (Milan)",
                    "country": "IT",
                    "timezone": "Europe/Rome",
                }
            ]
        raise AssertionError(path)


def test_equity_profile_resolves_primary_exchange():
    provider = FakeEulerpoolProvider()
    profile = provider.get_profile(
        InstrumentReference(
            symbol="LDO.MI",
            isin="IT0003856405",
            instrument_type=InstrumentType.STOCK,
        )
    )
    assert profile.exchange_code == "XMIL"
    assert profile.exchange_name == "Milan"


def test_fmp_402_is_capability_unavailable():
    request = httpx.Request("GET", "https://example.invalid")
    response = httpx.Response(402, request=request)
    error = httpx.HTTPStatusError(
        "Payment Required",
        request=request,
        response=response,
    )
    normalized = _normalize_http_error(error)
    assert isinstance(normalized, CapabilityUnavailableError)


@pytest.fixture
def quota_clock(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(FMPQuotaGuard, "_disabled_until", {})
    monkeypatch.setattr(fmp.time, "monotonic", lambda: clock[0])
    return clock


def test_fmp_quota_stops_retries_and_blocks_new_instances(quota_clock, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            429, json={"Error Message": "Limit Reach . Please upgrade your plan"}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = FMPProvider(
            "secret-api-key", client=client, retry_policy=RetryPolicy(max_retries=3),
            quota_cooldown_seconds=60,
        )
        with pytest.raises(ProviderRateLimitError) as error:
            provider.search("GOOG")
        assert get_http_status(error.value) == 429
        assert len(calls) == 1
        assert "secret-api-key" not in str(error.value)
        assert "secret-api-key" not in caplog.text

        second = FMPProvider("secret-api-key", client=client)
        with pytest.raises(ProviderRateLimitError, match="suspended"):
            second.get_profile(InstrumentReference(symbol="GOOG"))
        assert len(calls) == 1

        quota_clock[0] += 60
        with pytest.raises(ProviderRateLimitError):
            provider.search("GOOG")
        assert len(calls) == 2


@pytest.mark.parametrize("payload", [{"Error Message": "Too many requests"}, {}, []])
def test_fmp_normal_429_still_retries_without_suspension(quota_clock, payload):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json=payload) if len(calls) == 1 else httpx.Response(200, json=[])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = FMPProvider(
            "ordinary-key", client=client,
            retry_policy=RetryPolicy(max_retries=1, base_delay_seconds=0, jitter_seconds=0),
        )
        assert provider.search("GOOG") == []
        assert provider.search("GOOG") == []
    assert len(calls) == 3


def test_fmp_default_cooldown_and_key_isolation(quota_clock):
    guard = FMPQuotaGuard("exhausted-key")
    assert not guard.should_retry(
        httpx.Response(429, json={"Error Message": "Limit Reach ."})
    )
    FMPQuotaGuard("other-key").check_available()
    quota_clock[0] += 86399
    with pytest.raises(ProviderRateLimitError):
        guard.check_available()
    quota_clock[0] += 1
    guard.check_available()


def test_fmp_keeps_caller_retry_filter(quota_clock):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = FMPProvider(
            "caller-filter-key", client=client,
            retry_policy=RetryPolicy(max_retries=3, response_retry_filter=lambda _: False),
        )
        with pytest.raises(fmp.ProviderTemporaryError):
            provider.search("GOOG")
    assert len(calls) == 1


def test_fmp_job_client_shares_cooldown_with_provider(quota_clock, monkeypatch):
    from jobs.fmp import client as job_client

    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={"Error Message": "Limit Reach ."})
        return httpx.Response(200, json=[])

    values = {"FMP_QUOTA_COOLDOWN_SECONDS": "30", "FMP_HTTP_MAX_RETRIES": "3"}
    monkeypatch.setattr(
        job_client, "settings",
        SimpleNamespace(FMP_API_KEY="shared-key", get=values.get),
    )
    with httpx.Client(transport=httpx.MockTransport(handler), base_url="https://example.test") as client:
        monkeypatch.setattr(job_client, "create_http_client", lambda **kwargs: client)
        jobs = job_client.FMPClient()
        with pytest.raises(httpx.HTTPStatusError) as error:
            jobs.earnings_calendar("2026-10-01", "2026-10-28")
        assert get_http_status(error.value) == 429
        provider = FMPProvider("shared-key", client=client)
        with pytest.raises(ProviderRateLimitError, match="suspended"):
            provider.search("GOOG")
        with pytest.raises(ProviderRateLimitError, match="suspended"):
            jobs.dividends_calendar("2026-10-01", "2026-10-28")
        assert len(calls) == 1
        quota_clock[0] += 30
        assert jobs.earnings_calendar("2026-10-01", "2026-10-28") == []
        assert len(calls) == 2


def test_fmp_quota_activates_without_retryable_429(quota_clock):
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(429, json={"Error Message": "Limit Reach ."})
    )) as client:
        provider = FMPProvider(
            "no-retry-key", client=client,
            retry_policy=RetryPolicy(retryable_status_codes=frozenset()),
        )
        with pytest.raises(ProviderRateLimitError):
            provider.search("GOOG")
        with pytest.raises(ProviderRateLimitError, match="suspended"):
            provider.search("GOOG")
