from http_client import testing as httpx
from llm.base import LLMRequest
from llm.providers import gemini as module


class FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_gemini_temperature_is_configured_through_json_config(monkeypatch):
    captured = {}

    def fake_request(client, method, url, **kwargs):
        captured.update(kwargs["json"])
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {"parts": [{"text": '{"ok":true}'}]},
                    }
                ]
            },
        )

    monkeypatch.setattr(module, "create_http_client", FakeClient)
    monkeypatch.setattr(module, "request_with_retry", fake_request)

    provider = module.GeminiProvider(
        api_key="test-key",
        model="test-model",
        json_config={"generationConfig": {"temperature": 0.2}},
    )
    provider.generate_json(LLMRequest(prompt="Return JSON", temperature=0.0))

    assert captured["generationConfig"]["temperature"] == 0.2
