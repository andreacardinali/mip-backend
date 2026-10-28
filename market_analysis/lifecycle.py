from __future__ import annotations

from market_analysis.lifecycle_repository import SignalLifecycleRepository
from market_analysis.scan import ScanResult


def reconcile_persisted_signals(
    db, signal_ids: list[str], stability_minutes: int = 30
) -> list[dict]:
    repository = SignalLifecycleRepository(db)
    return [
        repository.reconcile(signal_id, stability_minutes) for signal_id in signal_ids
    ]


def expire_evaluated_signals(db, result: ScanResult, signal_ids: list[str], stability_minutes: int = 30) -> int:
    scopes = [
        {
            "instrument_id": evaluation.instrument_id,
            "strategy_code": strategy.upper(),
            "timeframe": evaluation.timeframe,
            "observed_at": evaluation.observed_at.isoformat(),
        }
        for evaluation in result.evaluations
        for strategy in evaluation.strategy_codes
    ]
    return SignalLifecycleRepository(db).expire_evaluated(scopes, signal_ids, stability_minutes)
