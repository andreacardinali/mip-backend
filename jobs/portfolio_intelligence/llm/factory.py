from jobs.portfolio_intelligence.llm.base import LLMProvider
from jobs.portfolio_intelligence.llm.gemini import GeminiProvider
from jobs.portfolio_intelligence.llm.ollama import OllamaProvider
from jobs.portfolio_intelligence.llm.openai_compatible import OpenAICompatibleProvider


def create_research_provider(config) -> LLMProvider:
    provider = config.llm_provider.strip().lower()

    if provider == "gemini":
        return GeminiProvider(
            api_key=config.llm_api_key,
            model=config.llm_model,
            timeout=config.llm_timeout,
            max_retries=config.llm_max_retries,
        )

    if provider == "ollama":
        return OllamaProvider(
            base_url=config.llm_base_url,
            model=config.llm_model,
            timeout=config.llm_timeout,
            max_retries=config.llm_max_retries,
        )

    if provider == "openai_compatible":
        return OpenAICompatibleProvider(
            base_url=config.llm_base_url,
            api_key=config.llm_api_key,
            model=config.llm_model,
            timeout=config.llm_timeout,
            max_retries=config.llm_max_retries,
        )

    raise ValueError(f"Unsupported INTELLIGENCE_LLM_PROVIDER: {provider}")

def create_validation_provider(config) -> LLMProvider:
    provider = config.validation_llm_provider.strip().lower()

    if provider == "gemini":
        return GeminiProvider(
            api_key=config.validation_llm_api_key,
            model=config.validation_llm_model,
            timeout=config.validation_llm_timeout,
            max_retries=config.validation_llm_max_retries,
        )
    
    if provider == "ollama":
        return OllamaProvider(
            base_url=config.validation_llm_base_url,
            model=config.validation_llm_model,
            timeout=config.validation_llm_timeout,
            max_retries=config.llm_max_retries,
        )

    if provider == "openai_compatible":
        return OpenAICompatibleProvider(
            base_url=config.validation_llm_base_url,
            api_key=config.validation_llm_api_key,
            model=config.validation_llm_model,
            timeout=config.validation_llm_timeout,
            max_retries=config.validation_llm_max_retries,
        )

    raise ValueError(f"Unsupported INTELLIGENCE_VALIDATION_LLM_PROVIDER: {provider}")
