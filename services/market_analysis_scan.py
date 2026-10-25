from __future__ import annotations

from typing import Any

from market_analysis.repository import MarketAnalysisRepository
from market_analysis.scan import ScanConfig, ScanMember, scan_members


def run_market_analysis_scan(
    db,
    *,
    market_data_service,
    universe_code: str,
    config: ScanConfig | None = None,
) -> dict[str, Any]:
    effective_config = config or ScanConfig()
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
        algorithm_version=effective_config.algorithm_version,
        config={
            "period": effective_config.period,
            "interval": effective_config.interval,
            "minimum_bars": effective_config.minimum_bars,
            "timeframe": effective_config.timeframe,
        },
    )

    try:
        result = scan_members(
            members,
            market_data_service=market_data_service,
            config=effective_config,
        )
        signal_ids = []
        for evaluated in result.candidates:
            signal_ids.append(
                repository.upsert_signal(
                    instrument_id=evaluated.member.instrument_id,
                    universe_id=evaluated.member.universe_id,
                    candidate=evaluated.candidate,
                    scores=evaluated.scores.to_dict(),
                    algorithm_version=effective_config.algorithm_version,
                )
            )

        for failure in result.failures:
            repository.record_scan_failure(
                run_id=run_id,
                instrument_id=failure.instrument_id,
                symbol=failure.symbol,
                reason=failure.reason,
            )

        summary = {
            **result.to_dict(),
            "run_id": run_id,
            "universe_code": universe_code.upper(),
            "persisted_signals": len(signal_ids),
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
