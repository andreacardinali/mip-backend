import json

from http_client import testing as httpx
from llm.base import LLMRequest
from llm.config import LLMConfig
from llm.providers import ollama as module


class Settings:
    def __init__(self, data):
        self.data = data

    def get(self, key, default=None):
        return self.data.get(key, default)


class FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _config(**values):
    data = {
        "TEST_PROVIDER": "ollama",
        "TEST_MODEL": "qwen3.5:9b",
        "TEST_BASE_URL": "http://ollama:11434",
        "TEST_CACHE_ENABLED": "false",
    }
    data.update(values)
    return LLMConfig.from_settings(
        Settings(data),
        prefix="TEST",
        default_provider="ollama",
        default_timeout_seconds=120,
    )


def test_think_defaults_to_false():
    assert _config().think_enabled is False


def test_think_is_loaded_from_consumer_prefix():
    assert _config(TEST_THINK_ENABLED="true").think_enabled is True


def test_invalid_think_value_is_rejected():
    try:
        _config(TEST_THINK_ENABLED="sometimes")
    except ValueError as error:
        assert "TEST_THINK_ENABLED must be a boolean value" in str(error)
    else:
        raise AssertionError("invalid prefixed THINK_ENABLED value must be rejected")


def test_ollama_sends_configured_think_value(monkeypatch):
    captured = {}

    def fake_request(client, method, url, **kwargs):
        captured.update(kwargs["json"])
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"response": json.dumps({"status": "ok"})},
        )

    monkeypatch.setattr(module, "create_http_client", FakeClient)
    monkeypatch.setattr(module, "request_with_retry", fake_request)
    provider = module.OllamaProvider(
        base_url="http://ollama.internal:11434",
        model="qwen3.5:9b",
        think=True,
    )
    response = provider.generate_json(LLMRequest(prompt="Return JSON"))
    assert captured["think"] is True
    assert response.data == {"status": "ok"}
