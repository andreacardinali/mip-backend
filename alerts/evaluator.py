from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any

from alerts.models import AlertCandidate, AlertMatch, AlertRule
from alerts.rule_configuration import effective_rule
from market_time.normalization import as_utc


def _upper_set(value: Any) -> set[str]:
    return {str(v).strip().upper() for v in value} if isinstance(value, list) else set()


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def observation_time(item: AlertCandidate) -> datetime | None:
    try:
        return as_utc(datetime.fromisoformat(item.observed_at or ""))
    except (ValueError, TypeError, OverflowError):
        return None


def candidate_is_eligible(
    rule: AlertRule, item: AlertCandidate, *, now: datetime | None = None
) -> bool:
    if item.symbol.upper() in _upper_set(rule.conditions.get("excluded_symbols")):
        return False
    allowed = _upper_set(rule.conditions.get("symbols"))
    if allowed and item.symbol.upper() not in allowed:
        return False
    if rule.alert_type != "MARKET_SIGNAL" and not _fresh(rule, item, now):
        return False
    if rule.alert_type in ("FIXED_PRICE", "PERCENT_MOVE"):
        if (
            item.last_price is None
            or not isfinite(item.last_price)
            or item.last_price <= 0
        ):
            return False
        if rule.alert_type == "PERCENT_MOVE" and (
            item.reference_price is None
            or not isfinite(item.reference_price)
            or item.reference_price <= 0
        ):
            return False
    return True


def _matches(rule: AlertRule, item: AlertCandidate) -> bool:
    c = rule.conditions
    if item.symbol.upper() in _upper_set(c.get("excluded_symbols")):
        return False
    if rule.alert_type in ("FIXED_PRICE", "PERCENT_MOVE"):
        allowed = _upper_set(c.get("symbols"))
        if allowed and item.symbol.upper() not in allowed:
            return False
        if (
            item.last_price is None
            or not isfinite(item.last_price)
            or item.last_price <= 0
        ):
            return False
        value = item.last_price
        if rule.alert_type == "PERCENT_MOVE":
            if (
                item.reference_price is None
                or not isfinite(item.reference_price)
                or item.reference_price <= 0
            ):
                return False
            value = (value / item.reference_price - 1) * 100
        threshold = _number(c.get("threshold"), float("nan"))
        if not isfinite(threshold) or threshold <= 0:
            return False
        direction = c.get(
            "price_direction", "ANY" if rule.alert_type == "PERCENT_MOVE" else "UP"
        )
        if direction == "ANY" and rule.alert_type == "PERCENT_MOVE":
            return abs(value) >= threshold
        if direction == "DOWN":
            return value <= (
                -threshold if rule.alert_type == "PERCENT_MOVE" else threshold
            )
        return direction == "UP" and value >= threshold
    checks = (
        ("symbols", item.symbol),
        ("directions", item.direction),
        ("verdicts", item.verdict or ""),
        ("origins", item.origin or ""),
    )
    for key, value in checks:
        allowed = _upper_set(c.get(key))
        if allowed and value.upper() not in allowed:
            return False
    strategies = _upper_set(c.get("strategy_codes"))
    if strategies and not strategies.intersection(
        code.upper() for code in item.strategy_codes
    ):
        return False
    if rule.alert_type == "SIGNAL_CHANGE":
        events = c.get("event_types", [])
        return item.signal_event_id is not None and (
            not events or item.event_type in events
        )
    return (
        item.composite_score >= _number(c.get("min_composite_score"), 0.75)
        and item.consensus_score >= _number(c.get("min_consensus_score"), 0.0)
        and item.conflict_score <= _number(c.get("max_conflict_score"), 1.0)
        and item.strategy_count >= int(_number(c.get("min_strategy_count"), 1))
    )


def _fingerprint(rule: AlertRule, item: AlertCandidate) -> str:
    state = {
        "rule": rule.rule_id,
        "instrument": item.instrument_id,
        "direction": item.direction.upper(),
        "verdict": (item.verdict or "").upper(),
        "score": round(item.composite_score, 2),
        "consensus": round(item.consensus_score, 2),
    }
    if rule.alert_type != "MARKET_SIGNAL":
        state.update(
            alert_type=rule.alert_type,
            observation=item.signal_event_id or item.observed_at,
        )
    return hashlib.sha256(
        json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def evaluate_alerts(
    rules: Iterable[AlertRule],
    candidates: Iterable[AlertCandidate],
    *,
    now: datetime | None = None,
) -> list[AlertMatch]:
    items = list(candidates)
    out = []
    for rule in rules:
        if rule.alert_type not in (
            "MARKET_SIGNAL",
            "FIXED_PRICE",
            "PERCENT_MOVE",
            "SIGNAL_CHANGE",
        ):
            continue
        for item in items:
            selected = effective_rule(rule, item.instrument_id)
            if selected is None:
                continue
            if not candidate_is_eligible(selected, item, now=now):
                continue
            if not _matches(selected, item):
                continue
            title = f"{item.symbol}: segnale {item.direction.upper()}"
            lines = [
                f"Regola: {rule.name}",
                f"Direzione: {item.direction.upper()}",
                f"Score composito: {item.composite_score:.3f}",
                f"Consenso: {item.consensus_score:.3f}",
                f"Conflitto: {item.conflict_score:.3f}",
                f"Strategie concordi: {item.strategy_count}",
            ]
            if item.verdict:
                lines.append(f"Verdetto: {item.verdict}")
            if item.summary:
                lines.extend(("", item.summary.strip()))
            if rule.alert_type in ("FIXED_PRICE", "PERCENT_MOVE"):
                title = f"{item.symbol}: soglia di prezzo"
                price_direction = selected.conditions.get(
                    "price_direction",
                    "ANY" if rule.alert_type == "PERCENT_MOVE" else "UP",
                )
                lines = [
                    f"Regola: {rule.name}",
                    f"Prezzo: {item.last_price}",
                    f"Soglia: {selected.conditions['threshold']} ({price_direction})",
                    f"Osservato: {item.observed_at}",
                ]
                if rule.alert_type == "PERCENT_MOVE":
                    change = (item.last_price / item.reference_price - 1) * 100
                    lines.append(f"Variazione: {change:+.2f}%")
            elif rule.alert_type == "SIGNAL_CHANGE":
                title = f"{item.symbol}: {item.event_type}"
                lines = [
                    f"Regola: {rule.name}",
                    f"Evento: {item.event_type}",
                    f"Direzione: {item.direction}",
                    f"Strategie: {', '.join(item.strategy_codes)}",
                    item.summary or "",
                ]
            context = {
                "alert_type": rule.alert_type,
                "scope": rule.conditions.get("scope", "SCREENER"),
                "scope_id": rule.conditions.get("scope_id"),
                "observed_at": item.observed_at,
                "last_price": item.last_price,
                "reference_price": item.reference_price,
                "reference_at": item.reference_at,
                "signal_event_id": item.signal_event_id,
                "event_type": item.event_type,
                "rule_id": rule.rule_id,
                "rule_name": rule.name,
                "signal_id": item.signal_id,
                "instrument_id": item.instrument_id,
                "symbol": item.symbol,
                "direction": item.direction,
                "origin": item.origin,
                "composite_score": item.composite_score,
                "consensus_score": item.consensus_score,
                "conflict_score": item.conflict_score,
                "strategy_count": item.strategy_count,
                "strategy_codes": list(item.strategy_codes),
                "verdict": item.verdict,
                "summary": item.summary,
                "evidence": item.evidence,
            }
            out.append(
                AlertMatch(
                    selected,
                    item,
                    _fingerprint(rule, item),
                    title,
                    "\n".join(lines),
                    context,
                )
            )
    return out


def _fresh(rule: AlertRule, item: AlertCandidate, now: datetime | None) -> bool:
    try:
        observed = observation_time(item)
        if observed is None:
            return False
        current = now or datetime.now(UTC)
        if (
            not timedelta(0)
            <= current - observed
            <= timedelta(minutes=rule.conditions.get("max_age_minutes", 60))
        ):
            return False
        if rule.alert_type == "PERCENT_MOVE":
            reference = datetime.fromisoformat(item.reference_at or "")
            return reference.tzinfo is not None and timedelta(
                0
            ) < observed - reference <= timedelta(days=7)
        return True
    except (ValueError, TypeError, OverflowError):
        return False
