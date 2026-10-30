from __future__ import annotations

import logging
from typing import Any

from config.settings import settings
from job_runtime.database import run_transactional
from market_analysis.llm_enrichment import SignalEnrichment
from market_analysis.llm_enrichment_repository import (
    MarketAnalysisLLMEnrichmentRepository,
)
from market_screener.coordination import run_coordinated_analysis
from services.market_analysis_llm_enrichment import (
    run_configured_market_analysis_llm_enrichment,
)

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
            "enrichment_fingerprint",
            "signal_ids",
            "workload",
        )
    }


def _load_pending_candidates(*, contract_fingerprint: str, limit: int):
    return run_transactional(
        lambda db: MarketAnalysisLLMEnrichmentRepository(db).list_pending(
            enrichment_fingerprint=contract_fingerprint, limit=limit
        )
    )


def _persist_single_enrichment(
    signal: dict[str, Any],
    enrichment: SignalEnrichment,
    analysis: dict[str, Any],
    composite_score: float,
    *,
    contract_fingerprint: str,
) -> bool:
    return run_transactional(
        lambda db: MarketAnalysisLLMEnrichmentRepository(db).apply(
            signal_id=str(signal["signal_id"]),
            enrichment_fingerprint=contract_fingerprint,
            llm_score=enrichment.llm_score,
            llm_analysis=analysis,
            composite_score=composite_score,
        )
    )


def run() -> dict[str, Any]:
    return run_coordinated_analysis(_run_enrichment)


def _run_enrichment() -> dict[str, Any]:
    result = run_configured_market_analysis_llm_enrichment(
        settings=settings,
        load_candidates=_load_pending_candidates,
        persist_enrichment=_persist_single_enrichment,
    )
    logger.info(
        "Market analysis LLM enrichment completed summary=%s", _compact_result(result)
    )
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(_compact_result(run()))
