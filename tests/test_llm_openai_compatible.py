import unittest

from llm.base import LLMRequest
from llm.config import LLMConfig
from llm.providers.openai_compatible import (
    OpenAICompatibleProvider,
    _deep_merge_dicts,
)


class OpenAICompatibleProviderTests(unittest.TestCase):
    def test_json_config_is_forwarded(
        self,
    ):
        provider = OpenAICompatibleProvider(
            base_url=("https://integrate.api.nvidia.com/v1"),
            api_key="test-key",
            model="google/gemma-4-31b-it",
            json_config={
                "chat_template_kwargs": {
                    "enable_thinking": False,
                },
                "stream": False,
                "top_p": 1,
                "seed": 0,
            },
        )

        body = provider._build_request_body(
            LLMRequest(
                prompt="Return JSON",
                temperature=0.0,
                max_tokens=256,
            )
        )

        self.assertEqual(
            body["model"],
            "google/gemma-4-31b-it",
        )

        self.assertEqual(
            body["temperature"],
            0.0,
        )

        self.assertEqual(
            body["max_tokens"],
            256,
        )

        self.assertEqual(
            body["chat_template_kwargs"],
            {
                "enable_thinking": False,
            },
        )

        self.assertFalse(body["stream"])
        self.assertEqual(body["top_p"], 1)
        self.assertEqual(body["seed"], 0)

        self.assertNotIn(
            "response_format",
            body,
        )

    def test_response_format_can_be_configured(
        self,
    ):
        provider = OpenAICompatibleProvider(
            base_url=("http://localhost:1234/v1"),
            api_key="",
            model="test-model",
            json_config={
                "response_format": {
                    "type": "json_object",
                },
            },
        )

        body = provider._build_request_body(
            LLMRequest(
                prompt="Return JSON",
            )
        )

        self.assertEqual(
            body["response_format"],
            {
                "type": "json_object",
            },
        )

    def test_nested_configuration_is_deep_merged(
        self,
    ):
        result = _deep_merge_dicts(
            {
                "provider_options": {
                    "routing": "balanced",
                    "flags": {
                        "a": True,
                        "b": False,
                    },
                },
            },
            {
                "provider_options": {
                    "flags": {
                        "b": True,
                        "c": True,
                    },
                },
            },
        )

        self.assertEqual(
            result,
            {
                "provider_options": {
                    "routing": "balanced",
                    "flags": {
                        "a": True,
                        "b": True,
                        "c": True,
                    },
                },
            },
        )

    def test_provider_rejects_model_override(
        self,
    ):
        with self.assertRaisesRegex(
            ValueError,
            "cannot override: model",
        ):
            OpenAICompatibleProvider(
                base_url=("http://localhost:1234/v1"),
                api_key="test-key",
                model="test-model",
                json_config={
                    "model": "other-model",
                },
            )

    def test_provider_rejects_messages_override(
        self,
    ):
        with self.assertRaisesRegex(
            ValueError,
            "cannot override: messages",
        ):
            OpenAICompatibleProvider(
                base_url=("http://localhost:1234/v1"),
                api_key="test-key",
                model="test-model",
                json_config={
                    "messages": [],
                },
            )

    def test_config_parses_json_config(
        self,
    ):
        fake_settings = {
            "TEST_PROVIDER": ("openai_compatible"),
            "TEST_BASE_URL": ("https://integrate.api.nvidia.com/v1"),
            "TEST_API_KEY": "test-key",
            "TEST_MODEL": ("google/gemma-4-31b-it"),
            "TEST_JSON_CONFIG": (
                '{"chat_template_kwargs":{"enable_thinking":false},"stream":false}'
            ),
        }

        config = LLMConfig.from_settings(
            fake_settings,
            prefix="TEST",
            default_provider=("openai_compatible"),
            default_timeout=120,
        )

        self.assertEqual(
            config.json_config,
            {
                "chat_template_kwargs": {
                    "enable_thinking": False,
                },
                "stream": False,
            },
        )

    def test_config_accepts_dictionary_value(
        self,
    ):
        fake_settings = {
            "TEST_PROVIDER": ("openai_compatible"),
            "TEST_BASE_URL": ("http://localhost:1234/v1"),
            "TEST_API_KEY": "test-key",
            "TEST_MODEL": "test-model",
            "TEST_JSON_CONFIG": {
                "stream": False,
            },
        }

        config = LLMConfig.from_settings(
            fake_settings,
            prefix="TEST",
            default_provider=("openai_compatible"),
            default_timeout=60,
        )

        self.assertEqual(
            config.json_config,
            {
                "stream": False,
            },
        )

    def test_json_config_must_be_object(
        self,
    ):
        fake_settings = {
            "TEST_PROVIDER": ("openai_compatible"),
            "TEST_BASE_URL": ("http://localhost:1234/v1"),
            "TEST_API_KEY": "test-key",
            "TEST_MODEL": "test-model",
            "TEST_JSON_CONFIG": ('["not", "an", "object"]'),
        }

        with self.assertRaisesRegex(
            ValueError,
            "must contain a JSON object",
        ):
            LLMConfig.from_settings(
                fake_settings,
                prefix="TEST",
                default_provider=("openai_compatible"),
                default_timeout=60,
            )

    def test_invalid_json_config_is_rejected(
        self,
    ):
        fake_settings = {
            "TEST_PROVIDER": ("openai_compatible"),
            "TEST_BASE_URL": ("http://localhost:1234/v1"),
            "TEST_API_KEY": "test-key",
            "TEST_MODEL": "test-model",
            "TEST_JSON_CONFIG": ('{"stream":false'),
        }

        with self.assertRaisesRegex(
            ValueError,
            "must contain valid JSON",
        ):
            LLMConfig.from_settings(
                fake_settings,
                prefix="TEST",
                default_provider=("openai_compatible"),
                default_timeout=60,
            )

    def test_config_rejects_protected_key(
        self,
    ):
        fake_settings = {
            "TEST_PROVIDER": ("openai_compatible"),
            "TEST_BASE_URL": ("http://localhost:1234/v1"),
            "TEST_API_KEY": "test-key",
            "TEST_MODEL": "test-model",
            "TEST_JSON_CONFIG": ('{"max_tokens":8192}'),
        }

        with self.assertRaisesRegex(
            ValueError,
            "cannot override: max_tokens",
        ):
            LLMConfig.from_settings(
                fake_settings,
                prefix="TEST",
                default_provider=("openai_compatible"),
                default_timeout=60,
            )


if __name__ == "__main__":
    unittest.main()
