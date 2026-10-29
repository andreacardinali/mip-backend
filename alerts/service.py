from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from alerts.evaluator import candidate_is_eligible, evaluate_alerts, observation_time
from alerts.models import AlertCandidate
from alerts.repository import AlertRepository
from alerts.rule_configuration import effective_rule
from market_time.calendar import ExchangeCalendarsProvider
from market_time.models import MarketStatus
from market_time.repository import SQLExchangeHoursRepository
from market_time.service import MarketTimeService
from notifications.queue import NotificationQueue


def _candidate(r: dict[str, Any]) -> AlertCandidate:
    return AlertCandidate(
        str(r["signal_id"]) if r.get("signal_id") else None,
        str(r["instrument_id"]),
        str(r["symbol"]),
        str(r.get("direction") or "NEUTRAL"),
        str(r["origin"]) if r.get("origin") else None,
        float(r.get("composite_score") or 0) / 100,
        float(r.get("consensus_score") or 0) / 100,
        float(r.get("conflict_score") or 0) / 100,
        int(r.get("strategy_count") or 1),
        str(r["verdict"]) if r.get("verdict") else None,
        str(r["summary"]) if r.get("summary") else None,
        dict(r.get("evidence") or {}),
        tuple(r.get("strategy_codes") or ()),
        observed_at=str(r["observed_at"]) if r.get("observed_at") else None,
        last_price=float(r["last_price"]) if r.get("last_price") is not None else None,
        reference_price=(
            float(r["reference_price"])
            if r.get("reference_price") is not None
            else None
        ),
        reference_at=str(r["reference_at"]) if r.get("reference_at") else None,
        signal_event_id=str(r["signal_event_id"]) if r.get("signal_event_id") else None,
        event_type=str(r["event_type"]) if r.get("event_type") else None,
    )


def evaluate_and_enqueue_market_alerts(
    db: Session, *, max_candidates: int = 500, notifications_enabled: bool = True
) -> dict[str, Any]:
    repo = AlertRepository(db)
    rules = repo.list_enabled_rules()
    if not rules:
        return {
            "status": "SUCCESS",
            "rules": 0,
            "candidates": 0,
            "matches": 0,
            "enqueued": 0,
        }
    candidate_count = 0
    matches = []
    now = datetime.now(UTC)
    market_time = MarketTimeService(
        SQLExchangeHoursRepository(db), ExchangeCalendarsProvider()
    )
    for rule in rules:
        rows = repo.list_rule_candidates(rule.rule_id, limit=max_candidates)
        candidates = [_candidate(row) for row in rows]
        candidate_count += len(candidates)
        for candidate in candidates:
            selected = effective_rule(rule, candidate.instrument_id)
            if selected is None:
                continue
            session_key = None
            needs_clock = (
                selected.conditions.get("regular_session_only", False)
                or selected.conditions.get("once_per_session", False)
                or selected.alert_type == "PERCENT_MOVE"
            )
            if needs_clock:
                observed = observation_time(candidate)
                if observed is None or observed > now:
                    continue
                exchange = repo.instrument_exchange(candidate.instrument_id)
                if not exchange:
                    continue
                observed_clock = market_time.snapshot(exchange, at=observed)
                if observed_clock.status == MarketStatus.UNKNOWN:
                    continue
                session = observed_clock.session or observed_clock.previous_session
                if session is None:
                    continue
                session_key = f"{exchange}:{session.session_date}"
                if selected.conditions.get("regular_session_only"):
                    clock = market_time.snapshot(exchange, at=now)
                    if (
                        clock.status != MarketStatus.OPEN
                        or observed_clock.status != MarketStatus.OPEN
                        or clock.session is None
                        or clock.session.session_date != session.session_date
                    ):
                        continue
                if selected.alert_type == "PERCENT_MOVE":
                    previous = observed_clock.previous_session
                    if previous is None:
                        continue
                    reference = repo.session_reference(
                        candidate.instrument_id, previous.opens_at, previous.closes_at
                    )
                    if not reference:
                        continue
                    candidate = replace(
                        candidate,
                        reference_price=float(reference["close"]),
                        reference_at=str(reference["ts"]),
                    )
            if not candidate_is_eligible(selected, candidate, now=now):
                continue
            direction_key = selected.conditions.get(
                "price_direction", candidate.direction
            )
            if selected.alert_type == "PERCENT_MOVE" and direction_key == "ANY":
                direction_key = (
                    "UP"
                    if candidate.last_price >= candidate.reference_price
                    else "DOWN"
                )
            evaluated = evaluate_alerts([selected], [candidate], now=now)
            if selected.alert_type in (
                "FIXED_PRICE",
                "PERCENT_MOVE",
            ) and selected.conditions.get("crossing_only", False):
                state_key = hashlib.sha256(
                    json.dumps(
                        {
                            "threshold": selected.conditions.get("threshold"),
                            "direction": direction_key,
                            "reference_at": (
                                candidate.reference_at
                                if selected.alert_type == "PERCENT_MOVE"
                                else None
                            ),
                        },
                        sort_keys=True,
                    ).encode()
                ).hexdigest()
                generation = repo.observe_threshold(
                    rule_id=selected.rule_id,
                    instrument_id=candidate.instrument_id,
                    observed_at=candidate.observed_at,
                    matched=bool(evaluated),
                    state_key=state_key,
                )
                if generation is None:
                    continue
                evaluated = [
                    replace(match, fingerprint=f"{state_key}:{generation}")
                    for match in evaluated
                ]
            for match in evaluated:
                context = {
                    **match.context,
                    "entry_price": candidate.last_price,
                    "session_key": session_key,
                    "once_per_session": selected.conditions.get(
                        "once_per_session", False
                    ),
                    "direction_key": direction_key,
                }
                matches.append(replace(match, context=context))
    queue = NotificationQueue(db, enabled=notifications_enabled)
    registered = enqueued = duplicates = skipped = 0
    deliveries = []
    for m in matches:
        event_id = repo.register_match(
            rule_id=m.rule.rule_id,
            instrument_id=m.candidate.instrument_id,
            signal_id=m.candidate.signal_id,
            fingerprint=m.fingerprint,
            payload=m.context,
        )
        if not event_id:
            duplicates += 1
            continue
        registered += 1
        deliveries.append((m, event_id))
    groups = {}
    for match, event_id in deliveries:
        key = (
            match.rule.user_id,
            match.rule.channel,
            match.rule.alert_type,
            match.rule.severity,
            (
                None
                if match.rule.conditions.get("notification_mode") == "DIGEST"
                else event_id
            ),
        )
        groups.setdefault(key, []).append((match, event_id))
    for group in groups.values():
        m, event_id = group[0]
        digest = m.rule.conditions.get("notification_mode") == "DIGEST"
        ids = [identifier for _, identifier in group]
        digest_key = hashlib.sha256(":".join(sorted(ids)).encode()).hexdigest()
        result = queue.enqueue(
            user_id=m.rule.user_id,
            notification_key=(
                f"MARKET_ALERT_DIGEST:{digest_key}"
                if digest
                else f"MARKET_ALERT:{event_id}"
            ),
            notification_type=m.rule.alert_type,
            entity_id=None,
            symbol=None if digest else m.candidate.symbol,
            event_key=event_id,
            severity=m.rule.severity,
            title=f"Riepilogo alert ({len(group)})" if digest else m.title,
            message=(
                "\n\n".join(match.message for match, _ in group)
                if digest
                else m.message
            ),
            context=(
                {"events": [match.context for match, _ in group]}
                if digest
                else m.context
            ),
            channel=m.rule.channel,
        )
        if result.created and result.notification_id:
            for identifier in ids:
                repo.mark_enqueued(
                    alert_event_id=identifier, notification_id=result.notification_id
                )
            enqueued += 1
        else:
            for identifier in ids:
                repo.mark_notification_skipped(
                    alert_event_id=identifier, status=result.status.value
                )
            skipped += len(ids)
    return {
        "status": "SUCCESS",
        "rules": len(rules),
        "candidates": candidate_count,
        "matches": len(matches),
        "registered": registered,
        "enqueued": enqueued,
        "duplicates_or_cooldown": duplicates,
        "notification_skipped": skipped,
    }
