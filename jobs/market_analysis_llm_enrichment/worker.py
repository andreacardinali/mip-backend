from __future__ import annotations

import logging
from typing import Any

from config.settings import settings
from job_runtime.database import run_transactional
from llm import LLMConfig, create_provider
from market_analysis.llm_enrichment import PROMPT_VERSION, SignalEnrichment
from market_analysis.llm_enrichment_repository import (
    MarketAnalysisLLMEnrichmentRepository,
)
from services.market_analysis_llm_enrichment import run_market_analysis_llm_enrichment

logger = logging.getLogger(__name__)
PREFIX = "MARKET_ANALYSIS_ENRICHMENT"
LLM_PREFIX = f"{PREFIX}_LLM"


def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: result.get(key, [] if key == "signal_ids" else 0)
        for key in (
            "status",
            "selected",
            "unique",
            "enriched",
            "failed",
            "prompt_id",
            "signal_ids",
        )
    }


def _load_pending_candidates(*, prompt_id: str, limit: int) -> list[dict[str, Any]]:
    return run_transactional(
        lambda db: MarketAnalysisLLMEnrichmentRepository(db).list_pending(
            prompt_version=prompt_id, limit=limit
        )
    )


def _persist_single_enrichment(
    signal: dict[str, Any],
    enrichment: SignalEnrichment,
    analysis: dict[str, Any],
    composite_score: float,
    *,
    prompt_id: str,
) -> bool:
    return run_transactional(
        lambda db: MarketAnalysisLLMEnrichmentRepository(db).apply(
            signal_id=str(signal["signal_id"]),
            prompt_version=prompt_id,
            llm_score=enrichment.llm_score,
            llm_analysis=analysis,
            composite_score=composite_score,
        )
    )


def run() -> dict[str, Any]:
    config = LLMConfig.from_settings(
        settings,
        prefix=LLM_PREFIX,
        default_provider="ollama",
        default_timeout_seconds=120.0,
    )
    provider = create_provider(config)
    prompt_id = str(settings.get(f"{PREFIX}_PROMPT_ID", PROMPT_VERSION))
    candidates = _load_pending_candidates(
        prompt_id=prompt_id,
        limit=int(settings.get(f"{PREFIX}_RUN_MAX_SIGNALS", 25)),
    )
    result = run_market_analysis_llm_enrichment(
        provider=provider,
        candidates=candidates,
        persist_enrichment=lambda signal, enrichment, analysis, composite: _persist_single_enrichment(
            signal, enrichment, analysis, composite, prompt_id=prompt_id
        ),
        prompt_version=prompt_id,
        max_tokens=int(settings.get(f"{PREFIX}_LLM_REQUEST_MAX_OUTPUT_TOKENS", 1200)),
        call_batch_size=int(settings.get(f"{PREFIX}_LLM_REQUEST_MAX_SIGNALS", 4)),
    )
    logger.info(
        "Market analysis LLM enrichment completed summary=%s", _compact_result(result)
    )
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(_compact_result(run()))
