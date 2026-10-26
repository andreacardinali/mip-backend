from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from llm import LLMProvider
from market_analysis.llm_enrichment import (
    PROMPT_VERSION,
    SignalEnrichment,
    calculate_composite_score,
    enrich_signal,
)

logger = logging.getLogger(__name__)

PersistEnrichment = Callable[
    [dict[str, Any], SignalEnrichment, dict[str, Any], float],
    bool,
]


def run_market_analysis_llm_enrichment(
    *,
    provider: LLMProvider,
    candidates: Sequence[dict[str, Any]],
    persist_enrichment: PersistEnrichment,
    prompt_version: str = PROMPT_VERSION,
    max_tokens: int = 1200,
) -> dict[str, Any]:
    """Enrich signals without owning a database transaction.

    The caller supplies ``persist_enrichment``. The worker implements that
    callback with one short transaction per signal, so no database transaction
    remains open while the LLM provider is running.
    """
    enriched: list[str] = []
    failures: list[dict[str, str]] = []

    for signal in candidates:
        signal_id = str(signal["signal_id"])

        try:
            enrichment = enrich_signal(
                provider,
                signal,
                max_tokens=max_tokens,
            )
            analysis = enrichment.to_dict()
            analysis["prompt_version"] = prompt_version
            composite_score = calculate_composite_score(
                signal,
                enrichment.llm_score,
            )

            if not persist_enrichment(
                signal,
                enrichment,
                analysis,
                composite_score,
            ):
                raise RuntimeError("signal is no longer eligible for enrichment")

            enriched.append(signal_id)
            logger.info(
                "Market analysis LLM enrichment committed "
                "signal_id=%s llm_score=%s composite_score=%s",
                signal_id,
                enrichment.llm_score,
                composite_score,
            )
        except Exception as error:
            logger.exception(
                "LLM enrichment failed signal_id=%s",
                signal_id,
            )
            failures.append(
                {
                    "signal_id": signal_id,
                    "error": str(error)[:500],
                }
            )

    return {
        "status": "SUCCESS" if not failures else "PARTIAL",
        "selected": len(candidates),
        "enriched": len(enriched),
        "failed": len(failures),
        "prompt_version": prompt_version,
        "signal_ids": enriched,
        "failures": failures,
    }
