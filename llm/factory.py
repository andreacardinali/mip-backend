from llm.base import LLMProvider
from llm.cache import CachedLLMProvider, LLMCacheConfig, PostgresLLMCacheStore
from llm.config import LLMConfig
from llm.fingerprint import provider_config_fingerprint
from llm.persistent_queue import PersistentQueueConfig, PersistentQueuedProvider
from llm.providers.gemini import GeminiProvider
from llm.providers.ollama import OllamaProvider
from llm.providers.openai_compatible import OpenAICompatibleProvider


def create_provider(config: LLMConfig) -> LLMProvider:
    config.validate()
    if config.provider == "gemini":
        provider: LLMProvider = GeminiProvider(
            api_key=config.api_key,
            model=config.model,
            timeout=config.timeout_seconds,
            max_retries=config.max_retries,
            json_config=config.json_config,
        )
    elif config.provider == "ollama":
        provider = OllamaProvider(
            base_url=config.base_url,
            model=config.model,
            timeout=config.timeout_seconds,
            max_retries=config.max_retries,
            think=config.think_enabled,
            json_config=config.json_config,
        )
    elif config.provider == "openai_compatible":
        provider = OpenAICompatibleProvider(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            timeout=config.timeout_seconds,
            max_retries=config.max_retries,
            tokens_per_minute=0 if config.queue_enabled else config.tokens_per_minute,
            requests_per_minute=(
                0 if config.queue_enabled else config.requests_per_minute
            ),
            rate_limit_safety_factor=config.rate_limit_safety_factor,
            retry_max_delay=config.retry_max_seconds,
            json_config=config.json_config,
        )
    else:
        raise ValueError(f"Unsupported LLM provider: {config.provider}")
    from db import SessionLocal

    if config.queue_enabled:
        provider = PersistentQueuedProvider(
            provider,
            SessionLocal,
            provider_name=config.provider,
            model=config.model,
            config=PersistentQueueConfig(
                queue_key=f"{config.provider}:{config.model}",
                max_attempts=config.max_retries + 1,
                tokens_per_minute=config.tokens_per_minute,
                requests_per_minute=config.requests_per_minute,
                requests_per_day=config.requests_per_day,
                daily_reset_timezone=config.daily_reset_timezone,
                safety_factor=config.rate_limit_safety_factor,
                poll_seconds=config.queue_poll_seconds,
                stale_seconds=config.queue_stale_seconds,
                wait_timeout_seconds=config.queue_wait_timeout_seconds,
                lease_seconds=config.queue_lease_seconds,
                heartbeat_seconds=config.queue_heartbeat_seconds,
                maintenance_interval_seconds=config.queue_maintenance_interval_seconds,
                completed_retention_days=config.queue_completed_retention_days,
                failed_retention_days=config.queue_failed_retention_days,
                abandoned_retention_days=config.queue_abandoned_retention_days,
            ),
        )
    if config.cache_enabled:
        provider = CachedLLMProvider(
            provider,
            PostgresLLMCacheStore(SessionLocal),
            provider_name=config.provider,
            model=config.model,
            provider_config_fingerprint=provider_config_fingerprint(
                provider=config.provider,
                model=config.model,
                base_url=config.base_url,
                json_config=config.json_config,
                think_enabled=config.think_enabled,
            ),
            config=LLMCacheConfig(
                enabled=True,
                ttl_seconds=config.cache_ttl_seconds,
                grounded_ttl_seconds=config.cache_grounded_ttl_seconds,
                poll_seconds=config.cache_poll_seconds,
                wait_timeout_seconds=config.cache_wait_timeout_seconds,
                claim_stale_seconds=config.cache_claim_stale_seconds,
            ),
        )
    return provider
