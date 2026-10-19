from llm.base import LLMProvider
from llm.config import LLMConfig
from llm.providers.gemini import GeminiProvider
from llm.providers.ollama import OllamaProvider
from llm.providers.openai_compatible import OpenAICompatibleProvider


def create_provider(config: LLMConfig) -> LLMProvider:
    config.validate()

    if config.provider == "gemini":
        return GeminiProvider(
            api_key=config.api_key,
            model=config.model,
            timeout=config.timeout,
            max_retries=config.max_retries,
        )

    if config.provider == "ollama":
        return OllamaProvider(
            base_url=config.base_url,
            model=config.model,
            timeout=config.timeout,
            max_retries=config.max_retries,
        )

    if config.provider == "openai_compatible":
        return OpenAICompatibleProvider(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            timeout=config.timeout,
            max_retries=config.max_retries,
        )

    raise ValueError(f"Unsupported LLM provider: {config.provider}")
