from http_client import testing as httpx
from market_data.capabilities import InstrumentType
from market_data.exceptions import CapabilityUnavailableError
from market_data.models import InstrumentReference
from market_data.providers.eulerpool import EulerpoolProvider
from market_data.providers.fmp import _normalize_http_error


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
