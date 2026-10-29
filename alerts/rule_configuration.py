from math import isfinite
from typing import Any
from uuid import UUID


def validate_rule_payload(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "name",
        "severity",
        "channel",
        "conditions",
        "cooldown_minutes",
        "enabled",
        "alert_type",
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
    alert_type = payload.get("alert_type", "MARKET_SIGNAL")
    if alert_type not in (
        "MARKET_SIGNAL",
        "FIXED_PRICE",
        "PERCENT_MOVE",
        "SIGNAL_CHANGE",
    ):
        raise ValueError("Invalid alert type")
    filters = (
        "symbols",
        "excluded_symbols",
        "directions",
        "verdicts",
        "origins",
        "strategy_codes",
    )
    scores = {
        "min_composite_score": 0.75,
        "min_consensus_score": 0.0,
        "max_conflict_score": 1.0,
    }
    if not isinstance(conditions, dict) or conditions.keys() - {
        *filters,
        *scores,
        "min_strategy_count",
        "scope",
        "scope_id",
        "threshold",
        "price_direction",
        "max_age_minutes",
    }:
        raise ValueError("Invalid alert conditions")
    normalized: dict[str, Any] = {}
    scope = conditions.get("scope", "SCREENER")
    if scope not in ("SCREENER", "WATCHLIST", "PORTFOLIO", "ALL"):
        raise ValueError("Invalid monitoring scope")
    normalized["scope"] = scope
    scope_id = conditions.get("scope_id")
    if scope_id is not None:
        if scope not in ("WATCHLIST", "PORTFOLIO") or not isinstance(scope_id, str):
            raise ValueError("Scope ID requires a watchlist or portfolio")
        try:
            normalized["scope_id"] = str(UUID(scope_id))
        except ValueError as error:
            raise ValueError("Invalid scope ID") from error
    max_age = conditions.get("max_age_minutes", 60)
    if type(max_age) is not int or not 1 <= max_age <= 10080:
        raise ValueError("Invalid maximum data age")
    normalized["max_age_minutes"] = max_age
    if alert_type in ("FIXED_PRICE", "PERCENT_MOVE"):
        threshold = conditions.get("threshold")
        if (
            type(threshold) not in (int, float)
            or not 0 < threshold <= 1e15
            or not isfinite(threshold)
        ):
            raise ValueError("Threshold must be a finite positive number")
        direction = conditions.get(
            "price_direction", "ANY" if alert_type == "PERCENT_MOVE" else "UP"
        )
        if direction not in (
            ("UP", "DOWN", "ANY") if alert_type == "PERCENT_MOVE" else ("UP", "DOWN")
        ):
            raise ValueError("Invalid price direction")
        normalized.update(threshold=float(threshold), price_direction=direction)
    elif "threshold" in conditions or "price_direction" in conditions:
        raise ValueError("Price conditions require a price alert")
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
        "alert_type": alert_type,
        "severity": severity,
        "channel": channel,
        "conditions": normalized,
        "cooldown_minutes": cooldown,
        "enabled": enabled,
    }
