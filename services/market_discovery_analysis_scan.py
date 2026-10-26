from __future__ import annotations

from typing import Any

from market_analysis.lifecycle import reconcile_persisted_signals
from market_analysis.quant_prefilter import (
    QuantPrefilterConfig,
    prefilter_evaluated_signals,
)
from market_analysis.repository import MarketAnalysisRepository
from market_analysis.scan import ScanConfig, ScanMember, scan_members
from market_sources.discovery.scan_repository import DiscoveryScanRepository

DISCOVERY_SCAN_UNIVERSE_CODE = "DISCOVERY_SCAN"


def run_market_discovery_analysis_scan(
    db,
    *,
    market_data_service,
    scan_config: ScanConfig,
    quant_config: QuantPrefilterConfig,
    core_min_attention_score: float = 20,
    discovery_min_attention_score: float = 40,
    core_limit: int = 100,
    discovery_limit: int = 25,
) -> dict[str, Any]:
    discovery_repository = DiscoveryScanRepository(db)
    rows = discovery_repository.list_scan_candidates(
        core_min_attention_score=core_min_attention_score,
        discovery_min_attention_score=discovery_min_attention_score,
        core_limit=core_limit,
        discovery_limit=discovery_limit,
    )
    if not rows:
        return {
            "status": "SKIPPED",
            "reason": "NO_ELIGIBLE_CANDIDATES",
            "run_id": None,
            "eligible_candidates": 0,
            "processed": 0,
            "technical_candidates": 0,
            "quant_candidates": 0,
            "persisted_signals": 0,
            "lifecycle_events": 0,
            "failures": 0,
            "failed_symbols": [],
            "skipped": 0,
            "skipped_by_reason": {},
            "skipped_symbols": [],
            "skipped_insufficient_history": [],
            "skipped_details": [],
            "signals": [],
        }

    repository = MarketAnalysisRepository(db)
    scan_universe_id = repository.upsert_universe(
        code=DISCOVERY_SCAN_UNIVERSE_CODE,
        name="Market Discovery Scan",
        universe_type="DYNAMIC",
        source_system_code=None,
        active=True,
        config={
            "purpose": "discovery_scan_run_tracking",
            "membership_managed": False,
        },
    )
    run_id = repository.start_scan_run(
        universe_id=scan_universe_id,
        config={
            "period": scan_config.period,
            "interval": scan_config.interval,
            "minimum_bars": scan_config.minimum_bars,
            "timeframe": scan_config.timeframe,
            "core_min_attention_score": core_min_attention_score,
            "discovery_min_attention_score": discovery_min_attention_score,
            "core_limit": core_limit,
            "discovery_limit": discovery_limit,
            "quant_minimum_score": quant_config.minimum_score,
            "quant_limit": quant_config.limit,
        },
    )

    context_by_instrument = {str(row["instrument_id"]): row for row in rows}
    members = [
        ScanMember(
            universe_id=(
                str(row["universe_id"]) if row["universe_id"] is not None else ""
            ),
            instrument_id=str(row["instrument_id"]),
            entity_id=str(row["entity_id"]),
            symbol=str(row["symbol"]),
            exchange=row.get("exchange"),
            currency_code=row.get("currency_code"),
            isin=row.get("isin"),
        )
        for row in rows
    ]

    try:
        scan_result = scan_members(
            members,
            market_data_service=market_data_service,
            config=scan_config,
        )
        quant_results = prefilter_evaluated_signals(
            scan_result.candidates,
            config=quant_config,
        )

        signal_ids: list[str] = []
        for ranked in quant_results:
            evaluated = ranked.evaluated
            context = context_by_instrument[evaluated.member.instrument_id]
            evidence = dict(evaluated.candidate.evidence)
            evidence["market_discovery"] = {
                "candidate_id": str(context["candidate_id"]),
                "snapshot_id": str(context["snapshot_id"]),
                "origin": context["origin"],
                "attention_score": float(context["attention_score"]),
                "universe_codes": context["universe_codes"],
                "attention_evidence": context["attention_evidence"],
                "quant_score": ranked.quant_score,
                "quant_breakdown": ranked.breakdown,
            }
            candidate = evaluated.candidate.__class__(
                strategy_code=evaluated.candidate.strategy_code,
                timeframe=evaluated.candidate.timeframe,
                direction=evaluated.candidate.direction,
                signal_type=evaluated.candidate.signal_type,
                trend=evaluated.candidate.trend,
                reference_price=evaluated.candidate.reference_price,
                observed_at=evaluated.candidate.observed_at,
                confidence=evaluated.candidate.confidence,
                reasons=evaluated.candidate.reasons,
                evidence=evidence,
                invalidation_price=evaluated.candidate.invalidation_price,
                target_price=evaluated.candidate.target_price,
            )
            scores = evaluated.scores.to_dict()
            scores["quant"] = ranked.quant_score
            scores["attention"] = float(context["attention_score"])
            signal_ids.append(
                repository.upsert_signal(
                    instrument_id=evaluated.member.instrument_id,
                    universe_id=(evaluated.member.universe_id or None),
                    candidate=candidate,
                    scores=scores,
                )
            )

        lifecycle_events = reconcile_persisted_signals(
            db,
            signal_ids,
            stability_minutes=30,
        )

        for failure in scan_result.failures:
            repository.record_scan_failure(
                run_id=run_id,
                instrument_id=failure.instrument_id,
                symbol=failure.symbol,
                reason=failure.reason,
            )

        skipped_details = [item.to_dict() for item in scan_result.skipped]
        insufficient_history = [
            item.symbol
            for item in scan_result.skipped
            if item.reason.value == "INSUFFICIENT_HISTORY"
        ]
        summary = {
            "status": "SUCCESS",
            "run_id": run_id,
            "eligible_candidates": len(rows),
            "processed": scan_result.processed,
            "technical_candidates": len(scan_result.candidates),
            "quant_candidates": len(quant_results),
            "persisted_signals": len(signal_ids),
            "lifecycle_events": len(lifecycle_events),
            "failures": len(scan_result.failures),
            "failed_symbols": [item.symbol for item in scan_result.failures],
            "skipped": len(scan_result.skipped),
            "skipped_by_reason": scan_result.to_dict()["skipped_by_reason"],
            "skipped_symbols": [item.symbol for item in scan_result.skipped],
            "skipped_insufficient_history": insufficient_history,
            "skipped_details": skipped_details,
            "signals": [
                {
                    "symbol": item.evaluated.member.symbol,
                    "strategy_code": item.evaluated.candidate.strategy_code,
                    "signal_type": item.evaluated.candidate.signal_type.value,
                    "direction": item.evaluated.candidate.direction.value,
                    "quant_score": item.quant_score,
                }
                for item in quant_results
            ],
        }
        repository.finish_scan_run(
            run_id=run_id,
            status="COMPLETED",
            summary=summary,
        )
        return summary
    except Exception as error:
        repository.finish_scan_run(
            run_id=run_id,
            status="FAILED",
            summary={"error": str(error)[:1000]},
        )
        raise
