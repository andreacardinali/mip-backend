from __future__ import annotations

from market_analysis.lifecycle_repository import SignalLifecycleRepository


def reconcile_persisted_signals(
    db, signal_ids: list[str], stability_minutes: int = 30
) -> list[dict]:
    repository = SignalLifecycleRepository(db)
    return [
        repository.reconcile(signal_id, stability_minutes) for signal_id in signal_ids
    ]
