from llm.base import LLMProvider, LLMRequest, LLMResponse
from llm.cache import CachedLLMProvider, CacheLookup, LLMCacheConfig, identify_request


class FakeProvider(LLMProvider):
    def __init__(self):
        self.calls = 0

    def generate_json(self, request):
        self.calls += 1
        return LLMResponse(data={"value": self.calls}, raw={"call": self.calls})


class MemoryStore:
    def __init__(self):
        self.values = {}
        self.claims = set()

    def claim(self, **kwargs):
        key = kwargs["cache_key"]
        if key in self.values:
            return CacheLookup("HIT", self.values[key])
        if key not in self.claims:
            self.claims.add(key)
            return CacheLookup("CLAIM")
        return CacheLookup("WAIT")

    def complete(self, **kwargs):
        self.values[kwargs["cache_key"]] = kwargs["response"]
        self.claims.discard(kwargs["cache_key"])

    def release(self, **kwargs):
        self.claims.discard(kwargs["cache_key"])


def _cached(raw, provider_fingerprint="provider-a"):
    return CachedLLMProvider(
        raw,
        MemoryStore(),
        provider_name="ollama",
        model="qwen3:8b",
        provider_config_fingerprint=provider_fingerprint,
        config=LLMCacheConfig(),
    )


def test_exact_request_is_served_from_cache():
    raw = FakeProvider()
    cached = _cached(raw)
    request = LLMRequest(
        prompt="same",
        prompt_fingerprint="contract-a",
        response_schema={"type": "object"},
    )
    assert cached.generate_json(request).data == {"value": 1}
    assert cached.generate_json(request).data == {"value": 1}
    assert raw.calls == 1


def test_prompt_rendering_contract_and_provider_config_change_identity():
    base = LLMRequest(prompt="x", prompt_fingerprint="contract-a")
    first = identify_request(
        base, provider="ollama", model="a", provider_config_fingerprint="provider-a"
    )
    assert (
        first.request_fingerprint
        != identify_request(
            LLMRequest(prompt="y", prompt_fingerprint="contract-a"),
            provider="ollama",
            model="a",
            provider_config_fingerprint="provider-a",
        ).request_fingerprint
    )
    assert (
        first.request_fingerprint
        != identify_request(
            LLMRequest(prompt="x", prompt_fingerprint="contract-b"),
            provider="ollama",
            model="a",
            provider_config_fingerprint="provider-a",
        ).request_fingerprint
    )
    assert (
        first.request_fingerprint
        != identify_request(
            base, provider="ollama", model="a", provider_config_fingerprint="provider-b"
        ).request_fingerprint
    )


def test_bypass_calls_underlying_provider_every_time():
    raw = FakeProvider()
    cached = _cached(raw)
    request = LLMRequest(prompt="same", cache_bypass=True)
    cached.generate_json(request)
    cached.generate_json(request)
    assert raw.calls == 2
