from __future__ import annotations

from llm.config import LLMConfig
from llm.fingerprint import fingerprint, provider_config_fingerprint
from market_analysis.llm_enrichment import (
    BATCH_RESPONSE_SCHEMA,
    RESPONSE_SCHEMA,
    _instructions,
)


def enrichment_fingerprint(
    *, config: LLMConfig | None = None, max_tokens: int = 1200
) -> str:
    return fingerprint(
        {
            "provider_config": (
                provider_config_fingerprint(
                    provider=config.provider,
                    model=config.model,
                    base_url=config.base_url,
                    json_config=config.json_config,
                    think_enabled=config.think_enabled,
                )
                if config is not None
                else None
            ),
            "request": {
                "temperature": 0.0,
                "max_tokens": max_tokens,
                "grounded_search": False,
            },
            "single": {
                "instructions": _instructions(batch=False),
                "response_schema": RESPONSE_SCHEMA,
            },
            "batch": {
                "instructions": _instructions(batch=True),
                "response_schema": BATCH_RESPONSE_SCHEMA,
            },
            "composite_score": {
                "technical": "0.70",
                "financial": "0.10",
                "catalyst": "0.10",
                "llm": "0.10",
            },
        }
    )
