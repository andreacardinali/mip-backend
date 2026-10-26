import json

import httpx

from llm.base import LLMRequest
from llm.providers import ollama as module


class FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_ollama_passes_response_schema_to_format(monkeypatch):
    captured = {}
    schema = {
        "type": "object",
        "properties": {"llm_score": {"type": "number"}},
        "required": ["llm_score"],
    }

    def fake_request(client, method, url, **kwargs):
        captured.update(kwargs["json"])
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"response": json.dumps({"llm_score": 75})},
        )

    monkeypatch.setattr(module.httpx, "Client", FakeClient)
    monkeypatch.setattr(module, "request_with_retry", fake_request)

    provider = module.OllamaProvider(
        base_url="http://ollama.internal:11434",
        model="qwen3:8b",
    )
    response = provider.generate_json(
        LLMRequest(prompt="Evaluate", response_schema=schema)
    )

    assert captured["format"] == schema
    assert "JSON Schema" in captured["prompt"]
    assert response.data == {"llm_score": 75}
