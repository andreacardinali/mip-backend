from llm.config import LLMConfig


class Settings:
    def __init__(self, data):
        self.data = data

    def get(self, key, default=None):
        return self.data.get(key, default)


def test_cache_settings_are_loaded():
    config = LLMConfig.from_settings(
        Settings(
            {
                "TEST_PROVIDER": "ollama",
                "TEST_MODEL": "qwen3:8b",
                "TEST_BASE_URL": "http://ollama:11434",
                "TEST_CACHE_ENABLED": "true",
                "TEST_CACHE_TTL_SECONDS": "3600",
                "TEST_CACHE_GROUNDED_TTL_SECONDS": "120",
            }
        ),
        prefix="TEST",
        default_provider="ollama",
        default_timeout=120,
    )
    assert config.cache_enabled is True
    assert config.cache_ttl_seconds == 3600
    assert config.cache_grounded_ttl_seconds == 120
