from __future__ import annotations

import logging
from typing import Any

from config.settings import settings
from job_runtime.database import run_transactional
from llm import LLMConfig, create_provider
from market_analysis.llm_enrichment import (
    PROMPT_VERSION,
    SignalEnrichment,
)
from market_analysis.llm_enrichment_repository import (
    MarketAnalysisLLMEnrichmentRepository,
)
from services.market_analysis_llm_enrichment import (
    run_market_analysis_llm_enrichment,
)

logger = logging.getLogger(__name__)


def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result.get("status"),
        "selected": result.get("selected", 0),
        "enriched": result.get("enriched", 0),
        "failed": result.get("failed", 0),
        "prompt_version": result.get("prompt_version"),
        "signal_ids": result.get("signal_ids", []),
    }


def _load_pending_candidates(
    *,
    prompt_version: str,
    limit: int,
    algorithm_version: str | None,
) -> list[dict[str, Any]]:
    return run_transactional(
        lambda db: MarketAnalysisLLMEnrichmentRepository(db).list_pending(
            prompt_version=prompt_version,
            limit=limit,
            algorithm_version=algorithm_version,
        )
    )


def _persist_single_enrichment(
    signal: dict[str, Any],
    enrichment: SignalEnrichment,
    analysis: dict[str, Any],
    composite_score: float,
    *,
    prompt_version: str,
) -> bool:
    signal_id = str(signal["signal_id"])

    return run_transactional(
        lambda db: MarketAnalysisLLMEnrichmentRepository(db).apply(
            signal_id=signal_id,
            prompt_version=prompt_version,
            llm_score=enrichment.llm_score,
            llm_analysis=analysis,
            composite_score=composite_score,
        )
    )


def run() -> dict[str, Any]:
    config = LLMConfig.from_settings(
        settings,
        prefix="MARKET_ANALYSIS_LLM",
        default_provider="ollama",
        default_timeout=120.0,
    )
    provider = create_provider(config)
    prompt_version = str(
        settings.get(
            "MARKET_ANALYSIS_LLM_PROMPT_VERSION",
            PROMPT_VERSION,
        )
    )
    algorithm_version = (
        str(
            settings.get(
                "MARKET_ANALYSIS_LLM_ALGORITHM_VERSION",
                "",
            )
        ).strip()
        or None
    )
    candidates = _load_pending_candidates(
        prompt_version=prompt_version,
        limit=int(
            settings.get(
                "MARKET_ANALYSIS_LLM_BATCH_SIZE",
                25,
            )
        ),
        algorithm_version=algorithm_version,
    )

    result = run_market_analysis_llm_enrichment(
        provider=provider,
        candidates=candidates,
        persist_enrichment=lambda signal, enrichment, analysis, composite: (
            _persist_single_enrichment(
                signal,
                enrichment,
                analysis,
                composite,
                prompt_version=prompt_version,
            )
        ),
        prompt_version=prompt_version,
        max_tokens=int(
            settings.get(
                "MARKET_ANALYSIS_LLM_MAX_TOKENS",
                1200,
            )
        ),
    )

    logger.info(
        "Market analysis LLM enrichment completed summary=%s",
        _compact_result(result),
    )
    logger.debug(
        "Market analysis LLM enrichment details=%s",
        result,
    )
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(_compact_result(run()))
