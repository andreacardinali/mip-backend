from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from market_analysis.scan import ScanConfig, ScanMember
from market_screener.api_repository import MarketScreenerApiRepository
from market_screener.config import ScreenerRankingConfig
from market_screener.ranking import rank_opportunities
from market_screener.repository import MarketScreenerRepository
from market_screener.run_repository import MarketScreenerRunRepository
from notifications.queue import NotificationQueue
from services.market_analysis_scan import run_member_analysis_scan


def analyze_screener_context(
    db: Session,
    *,
    user_id: str,
    context_type: str,
    context_id: str,
    market_data_service,
    config: ScanConfig,
    llm_enabled: bool = False,
    llm_settings=None,
    incremental: bool = False,
) -> dict[str, Any]:
    repository = MarketScreenerApiRepository(db)
    context = dict(user_id=user_id, context_type=context_type, context_id=context_id)
    instruments = repository.context_instruments(**context)
    if not instruments:
        raise ValueError("Il contesto non contiene strumenti da analizzare")
    if len(instruments) > 1000:
        raise ValueError("Il contesto supera il limite di 1000 strumenti")
    universe_id = repository.prepare_context_scan(**context)
    runs = MarketScreenerRunRepository(db)
    run_id = runs.start()

    def progress(completed, total, symbol):
        runs.progress(
            run_id=run_id,
            phase_results={
                "context": context,
                "progress": {
                    "phase": "TECHNICAL",
                    "completed": completed,
                    "total": total,
                    "symbol": symbol,
                },
            },
        )
        db.commit()

    scan = run_member_analysis_scan(
        db,
        market_data_service=market_data_service,
        universe_id=universe_id,
        universe_code=f"CONTEXT_{context_type}_{context_id}",
        members=[ScanMember(universe_id=universe_id, **item) for item in instruments],
        config=config,
        **({"incremental": True, "on_progress": progress} if incremental else {}),
    )
    phases = {"context": context, "analysis_scan": scan}
    status = scan["status"]
    if llm_enabled:
        from market_analysis.llm_enrichment_repository import (
            MarketAnalysisLLMEnrichmentRepository,
        )
        from services.market_analysis_llm_enrichment import (
            run_configured_market_analysis_llm_enrichment,
        )

        enrichment_repository = MarketAnalysisLLMEnrichmentRepository(db)

        def load_candidates(*, contract_fingerprint, limit):
            with db.begin_nested():
                return enrichment_repository.list_pending_context(
                    **context,
                    enrichment_fingerprint=contract_fingerprint,
                    limit=limit,
                )

        def persist(signal, enrichment, analysis, composite, *, contract_fingerprint):
            with db.begin_nested():
                persisted = enrichment_repository.apply(
                    signal_id=str(signal["signal_id"]),
                    enrichment_fingerprint=contract_fingerprint,
                    llm_score=enrichment.llm_score,
                    llm_analysis=analysis,
                    composite_score=composite,
                )
            if incremental:
                db.commit()
            return persisted

        try:
            phases["llm_enrichment"] = run_configured_market_analysis_llm_enrichment(
                settings=llm_settings,
                load_candidates=load_candidates,
                persist_enrichment=persist,
            )
        except Exception:
            phases["llm_enrichment"] = {
                "status": "FAILED",
                "reason": "enrichment_unavailable",
            }
        if phases["llm_enrichment"]["status"] != "SUCCESS" and status == "SUCCESS":
            status = "PARTIAL"
    ranking = build_market_screener_ranking(
        MarketScreenerRepository(db), config=ScreenerRankingConfig()
    )
    phases["ranking"] = ranking
    snapshot_id = runs.create_snapshot(run_id=run_id)
    runs.complete(
        run_id=run_id,
        status=status,
        discovery_snapshot_id=None,
        scan_run_id=scan["run_id"],
        phase_results=phases,
    )
    return {
        "status": status,
        "run_id": str(run_id),
        "ranking_snapshot_id": str(snapshot_id),
        "scan": scan,
        "ranking": ranking,
        **({"llm_enrichment": phases["llm_enrichment"]} if llm_enabled else {}),
    }


def enqueue_snapshot_summary(
    db: Session, *, snapshot_id: str, enabled: bool
) -> dict[str, Any]:
    if not enabled:
        return {"status": "DISABLED"}
    repository = MarketScreenerApiRepository(db)
    comparison = repository.get_snapshot_changes(snapshot_id)
    if comparison is None:
        return {"status": "SKIPPED", "reason": "incomplete_snapshot"}
    if not comparison.get("previous_snapshot_id"):
        return {"status": "SKIPPED", "reason": "initial_snapshot"}
    counts = {
        key: int(comparison[key])
        for key in ("new_count", "changed_count", "removed_count")
    }
    if not any(counts.values()):
        return {"status": "SKIPPED", "reason": "no_changes"}
    context = {
        "ranking_snapshot_id": str(comparison["ranking_snapshot_id"]),
        "previous_snapshot_id": str(comparison["previous_snapshot_id"]),
        **counts,
    }
    message = (
        f"Nuove entrate: {counts['new_count']}\n"
        f"Cambi del segnale: {counts['changed_count']}\n"
        f"Uscite: {counts['removed_count']}"
    )
    queue = NotificationQueue(db, enabled=enabled)
    outcomes: dict[str, int] = {}
    for user_id in repository.list_summary_recipients():
        result = queue.enqueue(
            user_id=user_id,
            notification_key=f"SCREENER_SUMMARY:{user_id}:{snapshot_id}",
            notification_type="SCREENER_SUMMARY",
            entity_id=None,
            symbol=None,
            event_key=snapshot_id,
            severity="LOW",
            title="Riepilogo screener",
            message=message,
            context=context,
        )
        outcomes[result.status] = outcomes.get(result.status, 0) + 1
    return {"status": "SUCCESS", "counts": counts, "outcomes": outcomes}


def build_market_screener_ranking(
    repository: MarketScreenerRepository, *, config: ScreenerRankingConfig
) -> dict[str, Any]:
    rows = repository.list_ranking_inputs(limit=max(config.limit * 8, 200))
    ranked = rank_opportunities(rows, config=config)
    payload = [
        {
            "signal_id": item.signal_id,
            "instrument_id": item.instrument_id,
            "symbol": item.symbol,
            "direction": item.direction,
            "origin": item.origin,
            "attention_score": item.attention_score,
            "technical_score": item.technical_score,
            "quant_score": item.quant_score,
            "llm_score": item.llm_score,
            "composite_score": item.composite_score,
            "ranking_position": item.ranking_position,
            "verdict": item.verdict,
            "summary": item.summary,
            "evidence": item.evidence,
            "strategy_count": item.strategy_count,
            "bullish_strategy_count": item.bullish_strategy_count,
            "bearish_strategy_count": item.bearish_strategy_count,
            "consensus_direction": item.consensus_direction,
            "consensus_score": item.consensus_score,
            "conflict_score": item.conflict_score,
            "supporting_strategies": list(item.supporting_strategies),
            "opposing_strategies": list(item.opposing_strategies),
            "supporting_signal_ids": list(item.supporting_signal_ids),
        }
        for item in ranked
    ]
    persisted = repository.replace_ranking(opportunities=payload)
    consensus_persistence = None
    apply_consensus = getattr(repository, "apply_consensus", None)
    if payload and callable(apply_consensus):
        consensus_persistence = apply_consensus(opportunities=payload)
    return {
        "status": "SUCCESS",
        "input_signals": len(rows),
        "ranked_opportunities": len(ranked),
        "symbols": [item.symbol for item in ranked],
        "persistence": persisted,
        "consensus_persistence": consensus_persistence,
    }
