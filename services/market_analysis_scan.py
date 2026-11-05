from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
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
from market_analysis.scan import (
    ScanConfig,
    ScanFailure,
    ScanMember,
    ScanResult,
    scan_members,
)
from market_time.repository import SQLExchangeHoursRepository
from services.market_analysis_lake import save_scan_evaluations


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
    exchange_codes = tuple(
        sorted(
            {
                exchange
                for member in members
                if (exchange := getattr(member, "exchange", None))
            }
        )
    )
    exchange_timezones = SQLExchangeHoursRepository(db).get_exchange_timezones(
        exchange_codes
    )
    observed_at = datetime.now(UTC)
    run_id = repository.start_scan_run(
        universe_id=universe_id,
        config={**asdict(effective_config), "quant": asdict(quant_config)},
    )

    try:
        if incremental:
            db.commit()
            scan_parts = []
            for batch_start in range(
                0, len(members), effective_config.instrument_batch_size
            ):
                batch_members = members[
                    batch_start : batch_start + effective_config.instrument_batch_size
                ]
                if on_progress:
                    on_progress(batch_start, len(members), batch_members[0].symbol)
                batch_result = scan_members(
                    batch_members,
                    market_data_service=market_data_service,
                    config=effective_config,
                    observed_at=observed_at,
                    exchange_timezones=exchange_timezones,
                )
                scan_parts.append(batch_result)
            scanned = ScanResult(
                processed=len(members),
                candidates=tuple(
                    item for part in scan_parts for item in part.candidates
                ),
                failures=tuple(item for part in scan_parts for item in part.failures),
                skipped=tuple(item for part in scan_parts for item in part.skipped),
                evaluations=tuple(
                    item for part in scan_parts for item in part.evaluations
                ),
            )
        else:
            scanned = scan_members(
                members,
                market_data_service=market_data_service,
                config=effective_config,
                observed_at=observed_at,
                exchange_timezones=exchange_timezones,
            )

        quant_results = prefilter_evaluated_signals(
            scanned.candidates, config=quant_config
        )
        selected_candidates = {id(item.evaluated) for item in quant_results}
        persisted_candidates = []
        persisted_failures = []
        persisted_skipped = []
        persisted_evaluations = []
        lifecycle_events = []
        expired_signals = 0
        for index, member in enumerate(members):
            instrument_id = str(member.instrument_id)
            part = ScanResult(
                processed=1,
                candidates=tuple(
                    item
                    for item in scanned.candidates
                    if str(item.member.instrument_id) == instrument_id
                ),
                failures=tuple(
                    item
                    for item in scanned.failures
                    if str(item.instrument_id) == instrument_id
                ),
                skipped=tuple(
                    item
                    for item in scanned.skipped
                    if str(item.instrument_id) == instrument_id
                ),
                evaluations=tuple(
                    item
                    for item in scanned.evaluations
                    if str(item.instrument_id) == instrument_id
                ),
            )
            try:
                assessment_ids = save_scan_evaluations(
                    db,
                    repository,
                    run_id=run_id,
                    members=[member],
                    result=part,
                    config=effective_config,
                    quant_config=quant_config,
                )
                signal_ids = []
                for evaluated in part.candidates:
                    quant_score, breakdown = score_evaluated_signal(
                        evaluated, config=quant_config
                    )
                    candidate = replace(
                        evaluated.candidate,
                        evidence={
                            **evaluated.candidate.evidence,
                            **(
                                {
                                    "assessment_event_id": assessment_ids[instrument_id]
                                }
                                if instrument_id in assessment_ids
                                else {}
                            ),
                            "quant": {
                                "score": quant_score,
                                "breakdown": breakdown,
                                "llm_eligible": id(evaluated)
                                in selected_candidates,
                            },
                        },
                    )
                    signal_ids.append(
                        repository.upsert_signal(
                            instrument_id=member.instrument_id,
                            universe_id=member.universe_id,
                            candidate=candidate,
                            scores=evaluated.scores.to_dict(),
                        )
                    )
                events = (
                    reconcile_persisted_signals(
                        db, signal_ids, stability_minutes=30
                    )
                    if signal_ids
                    else []
                )
                expired = expire_evaluated_signals(db, part, signal_ids)
                for failure in part.failures:
                    repository.record_scan_failure(
                        run_id=run_id,
                        instrument_id=failure.instrument_id,
                        symbol=failure.symbol,
                        reason=failure.reason,
                    )
                db.commit()
            except Exception as error:
                db.rollback()
                failure = ScanFailure(
                    instrument_id=member.instrument_id,
                    symbol=member.symbol,
                    reason=f"Persistence failed: {error}"[:500],
                )
                repository.record_scan_failure(
                    run_id=run_id,
                    instrument_id=failure.instrument_id,
                    symbol=failure.symbol,
                    reason=failure.reason,
                )
                db.commit()
                persisted_failures.append(failure)
                part = ScanResult(processed=1, candidates=(), failures=(failure,))
            else:
                persisted_candidates.extend(part.candidates)
                persisted_failures.extend(part.failures)
                persisted_skipped.extend(part.skipped)
                persisted_evaluations.extend(part.evaluations)
                lifecycle_events.extend(events)
                expired_signals += expired
            if on_progress:
                on_progress(index + 1, len(members), member.symbol)

        result = ScanResult(
            processed=len(members),
            candidates=tuple(persisted_candidates),
            failures=tuple(persisted_failures),
            skipped=tuple(persisted_skipped),
            evaluations=tuple(persisted_evaluations),
        )

        summary = {
            **result.to_dict(),
            "status": "PARTIAL" if result.failures else "SUCCESS",
            "run_id": run_id,
            "universe_code": universe_code.upper(),
            "persisted_signals": len(persisted_candidates),
            "quant_candidates": sum(
                id(evaluated) in selected_candidates
                for evaluated in persisted_candidates
            ),
            "lifecycle_events": len(lifecycle_events),
            "expired_signals": expired_signals,
        }
        repository.finish_scan_run(run_id=run_id, status="COMPLETED", summary=summary)
        db.commit()
        return summary
    except Exception as error:
        db.rollback()
        repository.finish_scan_run(
            run_id=run_id, status="FAILED", summary={"error": str(error)[:1000]}
        )
        db.commit()
        raise
