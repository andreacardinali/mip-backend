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
    enrich_signals_batch,
)

logger = logging.getLogger(__name__)

PersistEnrichment = Callable[
    [dict[str, Any], SignalEnrichment, dict[str, Any], float], bool
]


def _deduplicate_candidates(
    candidates: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    unique: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    seen: set[str] = set()
    for signal in candidates:
        signal_id = str(signal.get("signal_id", "")).strip()
        if not signal_id:
            failures.append(
                {"signal_id": "", "error": "candidate is missing signal_id"}
            )
            continue
        if signal_id in seen:
            failures.append(
                {"signal_id": signal_id, "error": "duplicate signal_id ignored"}
            )
            continue
        seen.add(signal_id)
        unique.append(signal)
    return unique, failures


def _enrich_resilient(
    *,
    provider: LLMProvider,
    signals: Sequence[dict[str, Any]],
    max_tokens: int,
) -> tuple[list[tuple[dict[str, Any], SignalEnrichment]], list[dict[str, str]]]:
    if not signals:
        return [], []
    if len(signals) == 1:
        signal = signals[0]
        signal_id = str(signal["signal_id"])
        try:
            return [
                (signal, enrich_signal(provider, signal, max_tokens=max_tokens))
            ], []
        except Exception as error:
            logger.exception("LLM enrichment failed signal_id=%s", signal_id)
            return [], [{"signal_id": signal_id, "error": str(error)[:500]}]

    try:
        enrichments = enrich_signals_batch(provider, signals, max_tokens=max_tokens)
        return list(zip(signals, enrichments, strict=True)), []
    except Exception as error:
        midpoint = len(signals) // 2
        logger.warning(
            "LLM enrichment batch failed size=%s; splitting %s+%s error=%s",
            len(signals),
            midpoint,
            len(signals) - midpoint,
            error,
        )
        left_ok, left_failures = _enrich_resilient(
            provider=provider,
            signals=signals[:midpoint],
            max_tokens=max_tokens,
        )
        right_ok, right_failures = _enrich_resilient(
            provider=provider,
            signals=signals[midpoint:],
            max_tokens=max_tokens,
        )
        return left_ok + right_ok, left_failures + right_failures


def run_market_analysis_llm_enrichment(
    *,
    provider: LLMProvider,
    candidates: Sequence[dict[str, Any]],
    persist_enrichment: PersistEnrichment,
    prompt_version: str = PROMPT_VERSION,
    max_tokens: int = 1200,
    call_batch_size: int = 4,
) -> dict[str, Any]:
    """Enrich signals in resilient provider batches and persist each result separately."""
    unique, failures = _deduplicate_candidates(candidates)
    enriched: list[str] = []
    batch_size = max(1, int(call_batch_size))

    for offset in range(0, len(unique), batch_size):
        successful, batch_failures = _enrich_resilient(
            provider=provider,
            signals=unique[offset : offset + batch_size],
            max_tokens=max_tokens,
        )
        failures.extend(batch_failures)

        for signal, enrichment in successful:
            signal_id = str(signal["signal_id"])
            try:
                analysis = enrichment.to_dict()
                analysis["prompt_version"] = prompt_version
                composite_score = calculate_composite_score(
                    signal, enrichment.llm_score
                )
                if not persist_enrichment(
                    signal, enrichment, analysis, composite_score
                ):
                    raise RuntimeError("signal is no longer eligible for enrichment")
                enriched.append(signal_id)
                logger.info(
                    "Market analysis LLM enrichment committed signal_id=%s "
                    "llm_score=%s composite_score=%s",
                    signal_id,
                    enrichment.llm_score,
                    composite_score,
                )
            except Exception as error:
                logger.exception(
                    "LLM enrichment persistence failed signal_id=%s", signal_id
                )
                failures.append({"signal_id": signal_id, "error": str(error)[:500]})

    return {
        "status": "SUCCESS" if not failures else "PARTIAL",
        "selected": len(candidates),
        "unique": len(unique),
        "enriched": len(enriched),
        "failed": len(failures),
        "prompt_version": prompt_version,
        "signal_ids": enriched,
        "failures": failures,
    }
