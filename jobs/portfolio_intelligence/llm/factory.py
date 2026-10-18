from jobs.portfolio_intelligence.llm.base import (
    LLMProvider,
)
from jobs.portfolio_intelligence.llm.gemini import (
    GeminiProvider,
)
from jobs.portfolio_intelligence.llm.openai_compatible import (
    OpenAICompatibleProvider,
)


def create_research_provider(
    config,
) -> LLMProvider:

    provider = (
        config.llm_provider
        .strip()
        .lower()
    )

    if provider == "gemini":
        return GeminiProvider(
            api_key=config.gemini_api_key,
            model=config.gemini_model,
            timeout=config.request_timeout,
            max_retries=
                config.gemini_max_retries,
        )

    if provider == "openai_compatible":
        return OpenAICompatibleProvider(
            base_url=config.llm_base_url,
            api_key=config.llm_api_key,
            model=config.llm_model,
            timeout=config.request_timeout,
        )

    raise ValueError(
        "Unsupported "
        "INTELLIGENCE_LLM_PROVIDER: "
        f"{config.llm_provider}"
    )

def create_validation_provider(
    config,
) -> LLMProvider:
    provider = (
        config.validation_llm_provider
        .strip()
        .lower()
    )

    if provider == "ollama":
        return OllamaProvider(
            base_url=(
                config.validation_llm_base_url
            ),
            model=config.validation_llm_model,
            timeout=max(
                config.request_timeout,
                120,
            ),
        )

    if provider == "openai_compatible":
        return OpenAICompatibleProvider(
            base_url=(
                config.validation_llm_base_url
            ),
            api_key=(
                config.validation_llm_api_key
            ),
            model=(
                config.validation_llm_model
            ),
            timeout=config.request_timeout,
        )

    raise ValueError(
        "Unsupported validation LLM provider: "
        f"{provider}"
    )