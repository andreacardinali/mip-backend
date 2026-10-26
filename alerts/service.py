from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from alerts.evaluator import evaluate_alerts
from alerts.models import AlertCandidate
from alerts.repository import AlertRepository
from market_screener.repository import MarketScreenerRepository
from notifications.queue import NotificationQueue


def _candidate(r: dict[str, Any]) -> AlertCandidate:
    return AlertCandidate(
        str(r["signal_id"]), str(r["instrument_id"]), str(r["symbol"]),
        str(r.get("direction") or "NEUTRAL"),
        str(r["origin"]) if r.get("origin") else None,
        float(r.get("composite_score") or 0), float(r.get("consensus_score") or 0),
        float(r.get("conflict_score") or 0), int(r.get("strategy_count") or 1),
        str(r["verdict"]) if r.get("verdict") else None,
        str(r["summary"]) if r.get("summary") else None,
        dict(r.get("evidence") or {}),
    )


def evaluate_and_enqueue_market_alerts(
    db: Session, *, max_candidates: int = 500, notifications_enabled: bool = True
) -> dict[str, Any]:
    repo = AlertRepository(db)
    rules = repo.list_enabled_rules()
    if not rules:
        return {"status": "SUCCESS", "rules": 0, "candidates": 0, "matches": 0, "enqueued": 0}
    rows = MarketScreenerRepository(db).list_ranking_inputs(limit=max_candidates)
    candidates = [_candidate(dict(r)) for r in rows]
    matches = evaluate_alerts(rules, candidates)
    queue = NotificationQueue(db, enabled=notifications_enabled)
    registered = enqueued = duplicates = skipped = 0
    for m in matches:
        event_id = repo.register_match(rule_id=m.rule.rule_id, instrument_id=m.candidate.instrument_id,
            signal_id=m.candidate.signal_id, fingerprint=m.fingerprint, payload=m.context)
        if not event_id:
            duplicates += 1
            continue
        registered += 1
        result = queue.enqueue(user_id=m.rule.user_id, notification_key=f"MARKET_ALERT:{event_id}",
            notification_type="MARKET_SIGNAL", entity_id=None, symbol=m.candidate.symbol,
            event_key=event_id, severity=m.rule.severity, title=m.title, message=m.message,
            context=m.context, channel=m.rule.channel)
        if result.created and result.notification_id:
            repo.mark_enqueued(alert_event_id=event_id, notification_id=result.notification_id)
            enqueued += 1
        else:
            skipped += 1
    return {"status": "SUCCESS", "rules": len(rules), "candidates": len(candidates),
        "matches": len(matches), "registered": registered, "enqueued": enqueued,
        "duplicates_or_cooldown": duplicates, "notification_skipped": skipped}
