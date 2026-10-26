from llm.base import LLMProvider, LLMRequest, LLMResponse
from llm.cache import (
    CachedLLMProvider,
    CacheLookup,
    LLMCacheConfig,
    build_cache_key,
    canonical_request_payload,
)


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


def test_exact_request_is_served_from_cache():
    raw = FakeProvider()
    cached = CachedLLMProvider(
        raw,
        MemoryStore(),
        provider_name="ollama",
        model="qwen3:8b",
        config=LLMCacheConfig(),
    )
    request = LLMRequest(prompt="same", response_schema={"type": "object"})
    assert cached.generate_json(request).data == {"value": 1}
    assert cached.generate_json(request).data == {"value": 1}
    assert raw.calls == 1


def test_schema_model_namespace_and_prompt_change_key():
    base = LLMRequest(prompt="x")
    key = build_cache_key(canonical_request_payload(base, provider="ollama", model="a"))
    assert key != build_cache_key(
        canonical_request_payload(base, provider="ollama", model="b")
    )
    assert key != build_cache_key(
        canonical_request_payload(
            LLMRequest(prompt="x", cache_namespace="v2"), provider="ollama", model="a"
        )
    )
    assert key != build_cache_key(
        canonical_request_payload(
            LLMRequest(prompt="x", response_schema={"type": "object"}),
            provider="ollama",
            model="a",
        )
    )


def test_bypass_calls_underlying_provider_every_time():
    raw = FakeProvider()
    cached = CachedLLMProvider(
        raw,
        MemoryStore(),
        provider_name="ollama",
        model="qwen3:8b",
        config=LLMCacheConfig(),
    )
    request = LLMRequest(prompt="same", cache_bypass=True)
    cached.generate_json(request)
    cached.generate_json(request)
    assert raw.calls == 2
