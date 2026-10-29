from math import isfinite
from typing import Any


def validate_rule_payload(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "name",
        "severity",
        "channel",
        "conditions",
        "cooldown_minutes",
        "enabled",
    }
    if payload.keys() - allowed:
        raise ValueError("Unknown alert rule fields")
    name = payload.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 160:
        raise ValueError("Name must contain between 1 and 160 characters")
    severity = payload.get("severity", "MEDIUM")
    if severity not in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
        raise ValueError("Invalid severity")
    channel = payload.get("channel")
    if channel not in (None, "TELEGRAM"):
        raise ValueError("Invalid channel")
    enabled = payload.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("Enabled must be a boolean")
    cooldown = payload.get("cooldown_minutes", 240)
    if type(cooldown) is not int or not 0 <= cooldown <= 2147483647:
        raise ValueError("Invalid cooldown")
    conditions = payload.get("conditions", {})
    filters = ("symbols", "directions", "verdicts", "origins", "strategy_codes")
    scores = {
        "min_composite_score": 0.75,
        "min_consensus_score": 0.0,
        "max_conflict_score": 1.0,
    }
    if not isinstance(conditions, dict) or conditions.keys() - {
        *filters,
        *scores,
        "min_strategy_count",
    }:
        raise ValueError("Invalid alert conditions")
    normalized: dict[str, Any] = {}
    for key in filters:
        values = conditions.get(key, [])
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise ValueError(f"{key} must be an array of non-empty codes")
        normalized[key] = list(dict.fromkeys(value.strip().upper() for value in values))
    for key, default in scores.items():
        value = conditions.get(key, default)
        if (
            type(value) not in (int, float)
            or not 0 <= value <= 1
            or not isfinite(value)
        ):
            raise ValueError(f"{key} must be between 0 and 1")
        normalized[key] = float(value)
    count = conditions.get("min_strategy_count", 1)
    if type(count) is not int or not 1 <= count <= 2147483647:
        raise ValueError("Minimum strategy count must be a positive integer")
    normalized["min_strategy_count"] = count
    return {
        "name": name.strip(),
        "severity": severity,
        "channel": channel,
        "conditions": normalized,
        "cooldown_minutes": cooldown,
        "enabled": enabled,
    }
