import unittest

from http_client import testing as httpx
from llm.base import LLMRequest
from llm.config import LLMConfig
from llm.json_config import deep_merge_dicts
from llm.providers import openai_compatible as openai_module
from llm.providers.openai_compatible import OpenAICompatibleProvider


class OpenAICompatibleProviderTests(unittest.TestCase):
    def test_json_config_is_forwarded(self):
        provider = OpenAICompatibleProvider(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key="test-key",
            model="google/gemma-4-31b-it",
            json_config={
                "chat_template_kwargs": {"enable_thinking": False},
                "stream": False,
                "top_p": 1,
                "seed": 0,
            },
        )
        body = provider._build_request_body(
            LLMRequest(prompt="Return JSON", temperature=0.0, max_tokens=256)
        )
        self.assertEqual(body["model"], "google/gemma-4-31b-it")
        self.assertEqual(body["temperature"], 0.0)
        self.assertEqual(body["max_tokens"], 256)
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertFalse(body["stream"])
        self.assertEqual(body["top_p"], 1)
        self.assertEqual(body["seed"], 0)
        self.assertNotIn("response_format", body)

    def test_response_format_can_be_configured(self):
        provider = OpenAICompatibleProvider(
            base_url="http://localhost:1234/v1",
            api_key="",
            model="test-model",
            json_config={"response_format": {"type": "json_object"}},
        )
        body = provider._build_request_body(LLMRequest(prompt="Return JSON"))
        self.assertEqual(body["response_format"], {"type": "json_object"})

    def test_responses_protocol_builds_responses_request(self):
        provider = OpenAICompatibleProvider(
            base_url="https://example.test/v1",
            api_key="test-key",
            model="test-model",
            api_protocol="responses",
        )
        body = provider._build_request_body(
            LLMRequest(prompt="Return JSON", temperature=0.1, max_tokens=300)
        )
        self.assertEqual(body["model"], "test-model")
        self.assertEqual(body["input"], "Return JSON")
        self.assertEqual(body["temperature"], 0.1)
        self.assertEqual(body["max_output_tokens"], 300)
        self.assertIn("JSON object", body["instructions"])
        self.assertNotIn("messages", body)

    def test_responses_extracts_output_text(self):
        self.assertEqual(
            OpenAICompatibleProvider._extract_responses_text(
                {"output_text": '{"ok":true}'}
            ),
            '{"ok":true}',
        )

    def test_responses_extracts_text_from_output_items(self):
        self.assertEqual(
            OpenAICompatibleProvider._extract_responses_text(
                {
                    "output": [
                        {"content": [{"type": "output_text", "text": '{"ok":'}]},
                        {"content": [{"type": "text", "text": "true}"}]},
                    ]
                }
            ),
            '{"ok":true}',
        )

    def test_responses_posts_to_responses_endpoint(self,):
        captured = {}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def fake_request(client, method, url, **kwargs):
            captured.update(method=method, url=url, body=kwargs["json"])
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={"output_text": '{"ok":true}'},
            )

        original_client = openai_module.create_http_client
        original_request = openai_module.request_with_retry
        openai_module.create_http_client = FakeClient
        openai_module.request_with_retry = fake_request
        try:
            provider = OpenAICompatibleProvider(
                base_url="https://example.test/v1",
                api_key="test-key",
                model="test-model",
                api_protocol="responses",
            )
            response = provider.generate_json(LLMRequest(prompt="Return JSON"))
        finally:
            openai_module.create_http_client = original_client
            openai_module.request_with_retry = original_request

        self.assertEqual(captured["url"], "https://example.test/v1/responses")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(response.data, {"ok": True})

    def test_nested_configuration_is_deep_merged(self):
        result = deep_merge_dicts(
            {
                "provider_options": {
                    "routing": "balanced",
                    "flags": {"a": True, "b": False},
                }
            },
            {"provider_options": {"flags": {"b": True, "c": True}}},
        )
        self.assertEqual(
            result,
            {
                "provider_options": {
                    "routing": "balanced",
                    "flags": {"a": True, "b": True, "c": True},
                }
            },
        )

    def test_provider_rejects_model_override(self):
        with self.assertRaisesRegex(ValueError, "cannot override: model"):
            OpenAICompatibleProvider(
                base_url="http://localhost:1234/v1",
                api_key="test-key",
                model="test-model",
                json_config={"model": "other-model"},
            )

    def test_provider_rejects_messages_override(self):
        with self.assertRaisesRegex(ValueError, "cannot override: messages"):
            OpenAICompatibleProvider(
                base_url="http://localhost:1234/v1",
                api_key="test-key",
                model="test-model",
                json_config={"messages": []},
            )

    def test_config_parses_json_config(self):
        config = LLMConfig.from_settings(
            {
                "TEST_PROVIDER": "openai_compatible",
                "TEST_BASE_URL": "https://integrate.api.nvidia.com/v1",
                "TEST_API_KEY": "test-key",
                "TEST_MODEL": "google/gemma-4-31b-it",
                "TEST_JSON_CONFIG": '{"stream":false}',
            },
            prefix="TEST",
            default_provider="openai_compatible",
            default_timeout_seconds=120,
        )
        self.assertEqual(config.json_config, {"stream": False})
        self.assertEqual(config.api_protocol, "chat_completions")

    def test_config_parses_responses_protocol(self):
        config = LLMConfig.from_settings(
            {
                "TEST_PROVIDER": "openai_compatible",
                "TEST_BASE_URL": "https://example.test/v1",
                "TEST_API_KEY": "test-key",
                "TEST_MODEL": "test-model",
                "TEST_API_PROTOCOL": "responses",
            },
            prefix="TEST",
            default_provider="openai_compatible",
            default_timeout_seconds=60,
        )
        self.assertEqual(config.api_protocol, "responses")

    def test_config_rejects_unknown_protocol(self):
        with self.assertRaisesRegex(ValueError, "Unsupported TEST_API_PROTOCOL"):
            LLMConfig.from_settings(
                {
                    "TEST_PROVIDER": "openai_compatible",
                    "TEST_BASE_URL": "https://example.test/v1",
                    "TEST_API_KEY": "test-key",
                    "TEST_MODEL": "test-model",
                    "TEST_API_PROTOCOL": "legacy",
                },
                prefix="TEST",
                default_provider="openai_compatible",
                default_timeout_seconds=60,
            )

    def test_config_accepts_dictionary_value(self):
        config = LLMConfig.from_settings(
            {
                "TEST_PROVIDER": "openai_compatible",
                "TEST_BASE_URL": "http://localhost:1234/v1",
                "TEST_API_KEY": "test-key",
                "TEST_MODEL": "test-model",
                "TEST_JSON_CONFIG": {"stream": False},
            },
            prefix="TEST",
            default_provider="openai_compatible",
            default_timeout_seconds=60,
        )
        self.assertEqual(config.json_config, {"stream": False})

    def test_json_config_must_be_object(self):
        with self.assertRaisesRegex(ValueError, "must contain a JSON object"):
            LLMConfig.from_settings(
                {
                    "TEST_PROVIDER": "openai_compatible",
                    "TEST_BASE_URL": "http://localhost:1234/v1",
                    "TEST_API_KEY": "test-key",
                    "TEST_MODEL": "test-model",
                    "TEST_JSON_CONFIG": '["not", "an", "object"]',
                },
                prefix="TEST",
                default_provider="openai_compatible",
                default_timeout_seconds=60,
            )

    def test_invalid_json_config_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must contain valid JSON"):
            LLMConfig.from_settings(
                {
                    "TEST_PROVIDER": "openai_compatible",
                    "TEST_BASE_URL": "http://localhost:1234/v1",
                    "TEST_API_KEY": "test-key",
                    "TEST_MODEL": "test-model",
                    "TEST_JSON_CONFIG": '{"stream":false',
                },
                prefix="TEST",
                default_provider="openai_compatible",
                default_timeout_seconds=60,
            )

    def test_config_rejects_protected_key(self):
        with self.assertRaisesRegex(ValueError, "cannot override: max_tokens"):
            LLMConfig.from_settings(
                {
                    "TEST_PROVIDER": "openai_compatible",
                    "TEST_BASE_URL": "http://localhost:1234/v1",
                    "TEST_API_KEY": "test-key",
                    "TEST_MODEL": "test-model",
                    "TEST_JSON_CONFIG": '{"max_tokens":8192}',
                },
                prefix="TEST",
                default_provider="openai_compatible",
                default_timeout_seconds=60,
            )


if __name__ == "__main__":
    unittest.main()
