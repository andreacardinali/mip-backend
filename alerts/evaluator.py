from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from alerts.models import AlertCandidate, AlertMatch, AlertRule


def _upper_set(value: Any) -> set[str]:
    return {str(v).strip().upper() for v in value} if isinstance(value, list) else set()


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _matches(rule: AlertRule, item: AlertCandidate) -> bool:
    c = rule.conditions
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
    return hashlib.sha256(
        json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def evaluate_alerts(
    rules: Iterable[AlertRule], candidates: Iterable[AlertCandidate]
) -> list[AlertMatch]:
    items = list(candidates)
    out = []
    for rule in rules:
        if rule.alert_type.upper() != "MARKET_SIGNAL":
            continue
        for item in items:
            if not _matches(rule, item):
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
            context = {
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
                    rule,
                    item,
                    _fingerprint(rule, item),
                    title,
                    "\n".join(lines),
                    context,
                )
            )
    return out
