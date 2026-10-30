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
from market_analysis.scan import ScanConfig, ScanMember, ScanResult, scan_members


def run_market_analysis_scan(
    db,
    *,
    market_data_service,
    universe_code: str,
    config: ScanConfig | None = None,
    quant_config: QuantPrefilterConfig | None = None,
    incremental: bool = True,
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

    return run_member_analysis_scan(
        db,
        market_data_service=market_data_service,
        universe_id=universe["universe_id"],
        universe_code=universe_code,
        members=members,
        config=effective_config,
        quant_config=quant_config,
        incremental=incremental,
    )


def run_member_analysis_scan(
    db,
    *,
    market_data_service,
    universe_id: str,
    universe_code: str,
    members: list[ScanMember],
    config: ScanConfig | None = None,
    quant_config: QuantPrefilterConfig | None = None,
    incremental: bool = False,
    on_progress=None,
) -> dict[str, Any]:
    if not members:
        raise ValueError("Scan has no members")
    effective_config = config or ScanConfig()
    quant_config = quant_config or QuantPrefilterConfig()
    repository = MarketAnalysisRepository(db)
    run_id = repository.start_scan_run(
        universe_id=universe_id,
        config={**asdict(effective_config), "quant": asdict(quant_config)},
    )

    try:
        member_lifecycle_events = []
        member_expired_signals = 0
        if incremental:
            db.commit()
            parts = []
            for index, member in enumerate(members):
                if on_progress:
                    on_progress(index, len(members), member.symbol)
                part = scan_members(
                    [member],
                    market_data_service=market_data_service,
                    config=effective_config,
                )
                member_signal_ids = []
                for evaluated in part.candidates:
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
                                "llm_eligible": False,
                            },
                        },
                    )
                    member_signal_ids.append(
                        repository.upsert_signal(
                            instrument_id=member.instrument_id,
                            universe_id=member.universe_id,
                            candidate=candidate,
                            scores=evaluated.scores.to_dict(),
                        )
                    )
                if member_signal_ids:
                    member_lifecycle_events.extend(
                        reconcile_persisted_signals(
                            db, member_signal_ids, stability_minutes=30
                        )
                    )
                member_expired_signals += expire_evaluated_signals(
                    db, part, member_signal_ids
                )
                for failure in part.failures:
                    repository.record_scan_failure(
                        run_id=run_id,
                        instrument_id=failure.instrument_id,
                        symbol=failure.symbol,
                        reason=failure.reason,
                    )
                db.commit()
                parts.append(part)
                if on_progress:
                    on_progress(index + 1, len(members), member.symbol)
            result = ScanResult(
                processed=len(members),
                candidates=tuple(item for part in parts for item in part.candidates),
                failures=tuple(item for part in parts for item in part.failures),
                skipped=tuple(item for part in parts for item in part.skipped),
                evaluations=tuple(item for part in parts for item in part.evaluations),
            )
        else:
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

        if incremental:
            lifecycle_events = member_lifecycle_events
            expired_signals = member_expired_signals
        else:
            lifecycle_events = (
                reconcile_persisted_signals(db, signal_ids, stability_minutes=30)
                if signal_ids
                else []
            )
            expired_signals = expire_evaluated_signals(db, result, signal_ids)

        for failure in (() if incremental else result.failures):
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
        if incremental:
            db.commit()
        return summary
    except Exception as error:
        if incremental:
            db.rollback()
        repository.finish_scan_run(
            run_id=run_id, status="FAILED", summary={"error": str(error)[:1000]}
        )
        if incremental:
            db.commit()
        raise
