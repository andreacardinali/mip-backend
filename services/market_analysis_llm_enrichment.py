from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

from httpx import InvalidURL

from http_client import HttpRequestError, get_http_status
from job_runtime.progress import BatchProgress
from llm import LLMConfig, LLMProvider, create_provider
from market_analysis.enrichment_contract import enrichment_fingerprint as fingerprint
from market_analysis.llm_enrichment import (
    SignalEnrichment,
    calculate_composite_score,
    enrich_signal,
    enrich_signals_batch,
)

logger = logging.getLogger(__name__)
PersistEnrichment = Callable[
    [dict[str, Any], SignalEnrichment, dict[str, Any], float], bool
]


def run_configured_market_analysis_llm_enrichment(
    *, settings, load_candidates, persist_enrichment
) -> dict[str, Any]:
    prefix = "MARKET_ANALYSIS_ENRICHMENT"
    try:
        config = LLMConfig.from_settings(
            settings,
            prefix=f"{prefix}_LLM",
            default_provider="ollama",
            default_timeout_seconds=120.0,
        )
        max_tokens = int(settings.get(f"{prefix}_LLM_REQUEST_MAX_OUTPUT_TOKENS", 1200))
        limit = int(settings.get(f"{prefix}_RUN_MAX_SIGNALS", 25))
        batch_size = int(settings.get(f"{prefix}_LLM_REQUEST_MAX_SIGNALS", 1))
        min_interval_minutes = int(settings.get(f"{prefix}_MIN_INTERVAL_MINUTES", 30))
        if min(max_tokens, limit, batch_size) < 1:
            raise ValueError("Enrichment limits must be positive")
        if min_interval_minutes < 0:
            raise ValueError("Enrichment minimum interval must be nonnegative")
    except (TypeError, ValueError):
        logger.warning("LLM enrichment unavailable: invalid configuration")
        return {"status": "FAILED", "reason": "invalid_configuration"}
    contract_fingerprint = fingerprint(config=config, max_tokens=max_tokens)
    candidates = load_candidates(
        contract_fingerprint=contract_fingerprint,
        limit=limit,
        min_interval_minutes=min_interval_minutes,
    )
    if not candidates:
        return {
            "status": "SUCCESS",
            "selected": 0,
            "enriched": 0,
            "failed": 0,
            "signal_ids": [],
            "enrichment_fingerprint": contract_fingerprint,
        }
    try:
        provider = create_provider(config)
    except (TypeError, ValueError):
        logger.warning("LLM enrichment unavailable: invalid provider configuration")
        return {"status": "FAILED", "reason": "invalid_configuration"}
    return run_market_analysis_llm_enrichment(
        provider=provider,
        candidates=candidates,
        persist_enrichment=lambda signal, enrichment, analysis, composite: persist_enrichment(
            signal,
            enrichment,
            analysis,
            composite,
            contract_fingerprint=contract_fingerprint,
        ),
        enrichment_fingerprint=contract_fingerprint,
        max_tokens=max_tokens,
        call_batch_size=batch_size,
    )


def _deduplicate_candidates(candidates):
    unique, failures, seen = [], [], set()
    for signal in candidates:
        signal_id = str(signal.get("signal_id", "")).strip()
        if not signal_id:
            failures.append(
                {"signal_id": "", "error": "candidate is missing signal_id"}
            )
        elif signal_id in seen:
            failures.append(
                {"signal_id": signal_id, "error": "duplicate signal_id ignored"}
            )
        else:
            seen.add(signal_id)
            unique.append(signal)
    return unique, failures


def _endpoint_unavailable(error):
    while error is not None:
        if get_http_status(error) is not None or isinstance(
            error, (HttpRequestError, InvalidURL, TimeoutError)
        ):
            return True
        error = error.__cause__
    return False


def _unavailable_failures(signals):
    return [
        {"signal_id": str(signal["signal_id"]), "error": "endpoint_unavailable"}
        for signal in signals
    ]


def _enrich_resilient(*, provider, signals, max_tokens, progress):
    if not signals:
        return [], [], False

    signal_ids = [str(signal["signal_id"]) for signal in signals]
    request_mode = "single" if len(signals) == 1 else "batch"
    progress.record_llm_call()
    call_number = progress.llm_calls
    started_at = time.perf_counter()

    logger.info(
        "LLM request started "
        "job=market_analysis_llm_enrichment "
        "call=%s mode=%s signal_count=%s signal_ids=%s "
        "max_output_tokens=%s",
        call_number,
        request_mode,
        len(signals),
        signal_ids,
        max_tokens,
    )

    if len(signals) == 1:
        signal = signals[0]
        signal_id = str(signal["signal_id"])
        try:
            enrichment = enrich_signal(
                provider,
                signal,
                max_tokens=max_tokens,
            )
            logger.info(
                "LLM request completed "
                "job=market_analysis_llm_enrichment "
                "call=%s mode=single signal_count=1 signal_ids=%s "
                "elapsed_seconds=%.3f",
                call_number,
                signal_ids,
                time.perf_counter() - started_at,
            )
            return [(signal, enrichment)], [], False
        except Exception as error:
            logger.exception(
                "LLM request failed "
                "job=market_analysis_llm_enrichment "
                "call=%s mode=single signal_count=1 signal_ids=%s "
                "elapsed_seconds=%.3f",
                call_number,
                signal_ids,
                time.perf_counter() - started_at,
            )
            if _endpoint_unavailable(error):
                return [], _unavailable_failures(signals), True
            return [], [{"signal_id": signal_id, "error": str(error)[:500]}], False

    try:
        enrichments = enrich_signals_batch(
            provider,
            signals,
            max_tokens=max_tokens,
        )
        logger.info(
            "LLM request completed "
            "job=market_analysis_llm_enrichment "
            "call=%s mode=batch signal_count=%s signal_ids=%s "
            "elapsed_seconds=%.3f",
            call_number,
            len(signals),
            signal_ids,
            time.perf_counter() - started_at,
        )
        return list(zip(signals, enrichments, strict=True)), [], False
    except Exception as error:
        if _endpoint_unavailable(error):
            logger.warning("LLM enrichment stopped: endpoint unavailable")
            return [], _unavailable_failures(signals), True
        midpoint = len(signals) // 2
        progress.record_split()
        logger.warning(
            "LLM request failed; splitting batch "
            "job=market_analysis_llm_enrichment "
            "call=%s mode=batch signal_count=%s signal_ids=%s "
            "left_size=%s right_size=%s elapsed_seconds=%.3f error=%s",
            call_number,
            len(signals),
            signal_ids,
            midpoint,
            len(signals) - midpoint,
            time.perf_counter() - started_at,
            error,
        )
        left_ok, left_failures, stopped = _enrich_resilient(
            provider=provider,
            signals=signals[:midpoint],
            max_tokens=max_tokens,
            progress=progress,
        )
        if stopped:
            return (
                left_ok,
                left_failures + _unavailable_failures(signals[midpoint:]),
                True,
            )
        right_ok, right_failures, stopped = _enrich_resilient(
            provider=provider,
            signals=signals[midpoint:],
            max_tokens=max_tokens,
            progress=progress,
        )
        return left_ok + right_ok, left_failures + right_failures, stopped


def run_market_analysis_llm_enrichment(
    *,
    provider: LLMProvider,
    candidates: Sequence[dict[str, Any]],
    persist_enrichment: PersistEnrichment,
    enrichment_fingerprint: str,
    max_tokens: int = 1200,
    call_batch_size: int = 1,
) -> dict[str, Any]:
    if not enrichment_fingerprint:
        raise ValueError("enrichment_fingerprint cannot be empty")
    unique, failures = _deduplicate_candidates(candidates)
    enriched = []
    batch_size = max(1, int(call_batch_size))
    progress = BatchProgress("market_analysis_llm_enrichment", len(unique), batch_size)
    progress.log_plan(logger)
    stopped = False
    for offset in range(0, len(unique), batch_size):
        current_batch = unique[offset : offset + batch_size]
        successful, batch_failures, stopped = _enrich_resilient(
            provider=provider,
            signals=current_batch,
            max_tokens=max_tokens,
            progress=progress,
        )
        failures.extend(batch_failures)
        persistence_failures = 0
        for signal, enrichment in successful:
            signal_id = str(signal["signal_id"])
            try:
                analysis = enrichment.to_dict()
                analysis["enrichment_fingerprint"] = enrichment_fingerprint
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
                persistence_failures += 1
                logger.exception(
                    "LLM enrichment persistence failed signal_id=%s", signal_id
                )
                failures.append({"signal_id": signal_id, "error": str(error)[:500]})
        progress.complete_batch(
            len(current_batch), failed_items=len(batch_failures) + persistence_failures
        )
        progress.log_progress(logger)
        if stopped:
            remaining = unique[offset + batch_size :]
            failures.extend(_unavailable_failures(remaining))
            for remaining_offset in range(0, len(remaining), batch_size):
                item_count = len(
                    remaining[remaining_offset : remaining_offset + batch_size]
                )
                progress.complete_batch(item_count, failed_items=item_count)
            break
    progress.log_completed(logger)
    return {
        "status": "SUCCESS" if not failures else "PARTIAL",
        "selected": len(candidates),
        "unique": len(unique),
        "enriched": len(enriched),
        "failed": len(failures),
        "enrichment_fingerprint": enrichment_fingerprint,
        "signal_ids": enriched,
        "failures": failures,
        "workload": progress.summary(),
        **({"reason": "endpoint_unavailable"} if stopped else {}),
    }
