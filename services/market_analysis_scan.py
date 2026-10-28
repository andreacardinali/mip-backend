from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any

from market_analysis.lifecycle import (
    expire_evaluated_signals,
    reconcile_persisted_signals,
)
from market_analysis.quant_prefilter import (
    QuantPrefilterConfig,
    prefilter_evaluated_signals,
    score_evaluated_signal,
)
from market_analysis.repository import MarketAnalysisRepository
from market_analysis.scan import ScanConfig, ScanMember, scan_members


def run_market_analysis_scan(
    db,
    *,
    market_data_service,
    universe_code: str,
    config: ScanConfig | None = None,
    quant_config: QuantPrefilterConfig | None = None,
) -> dict[str, Any]:
    effective_config = config or ScanConfig()
    quant_config = quant_config or QuantPrefilterConfig()
    repository = MarketAnalysisRepository(db)
    universe = repository.get_universe(code=universe_code)
    if universe is None:
        raise ValueError(f"Active universe not found: {universe_code}")

    members = [
        ScanMember(**item)
        for item in repository.list_universe_members(
            universe_id=universe["universe_id"]
        )
    ]
    if not members:
        raise ValueError(f"Active universe has no members: {universe_code}")

    run_id = repository.start_scan_run(
        universe_id=universe["universe_id"],
        config={**asdict(effective_config), "quant": asdict(quant_config)},
    )

    try:
        result = scan_members(
            members, market_data_service=market_data_service, config=effective_config
        )
        quant_results = prefilter_evaluated_signals(
            result.candidates, config=quant_config
        )
        selected_candidates = {id(item.evaluated) for item in quant_results}
        signal_ids = []
        for evaluated in result.candidates:
            quant_score, breakdown = score_evaluated_signal(
                evaluated, config=quant_config
            )
            candidate = replace(
                evaluated.candidate,
                evidence={
                    **evaluated.candidate.evidence,
                    "quant": {
                        "score": quant_score,
                        "breakdown": breakdown,
                        "llm_eligible": id(evaluated) in selected_candidates,
                    },
                },
            )
            signal_ids.append(
                repository.upsert_signal(
                    instrument_id=evaluated.member.instrument_id,
                    universe_id=evaluated.member.universe_id,
                    candidate=candidate,
                    scores=evaluated.scores.to_dict(),
                )
            )

        lifecycle_events = (
            reconcile_persisted_signals(db, signal_ids, stability_minutes=30)
            if signal_ids
            else []
        )
        expired_signals = expire_evaluated_signals(db, result, signal_ids)

        for failure in result.failures:
            repository.record_scan_failure(
                run_id=run_id,
                instrument_id=failure.instrument_id,
                symbol=failure.symbol,
                reason=failure.reason,
            )

        summary = {
            **result.to_dict(),
            "status": "PARTIAL" if result.failures else "SUCCESS",
            "run_id": run_id,
            "universe_code": universe_code.upper(),
            "persisted_signals": len(signal_ids),
            "quant_candidates": len(quant_results),
            "lifecycle_events": len(lifecycle_events),
            "expired_signals": expired_signals,
        }
        repository.finish_scan_run(run_id=run_id, status="COMPLETED", summary=summary)
        return summary
    except Exception as error:
        repository.finish_scan_run(
            run_id=run_id, status="FAILED", summary={"error": str(error)[:1000]}
        )
        raise
