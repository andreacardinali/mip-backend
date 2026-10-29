import json
from unittest.mock import Mock

import pytest

from alerts.repository import AlertRepository
from alerts.rule_configuration import validate_rule_payload


def test_list_rules_uses_user_scoped_stored_routine():
    database = Mock()
    database.execute.return_value.mappings.return_value = [
        {"rule_id": "rule-1", "name": "Entry", "enabled": False}
    ]
    result = AlertRepository(database).list_user_rules("user-1")
    statement, parameters = database.execute.call_args.args
    assert "sp_alert_list_user_rules" in str(statement)
    assert parameters == {"user_id": "user-1"}
    assert result[0]["enabled"] is False


def test_save_rule_keeps_owner_and_strategy_conditions():
    database = Mock()
    database.execute.return_value.scalar.return_value = "rule-1"
    payload = {
        "name": "Entry",
        "severity": "HIGH",
        "channel": None,
        "conditions": {"strategy_codes": ["CALL_ENTRY_SETUP"]},
        "cooldown_minutes": 240,
        "enabled": True,
    }
    assert (
        AlertRepository(database).save_user_rule("user-1", "rule-1", payload)
        == "rule-1"
    )
    statement, parameters = database.execute.call_args.args
    assert "sp_alert_upsert_rule" in str(statement)
    assert parameters["user_id"] == "user-1"
    assert parameters["rule_id"] == "rule-1"
    assert json.loads(parameters["conditions"]) == payload["conditions"]


def test_save_foreign_or_missing_rule_returns_none():
    database = Mock()
    database.execute.return_value.scalar.return_value = None
    payload = dict(
        name="Entry",
        severity="HIGH",
        channel=None,
        conditions={},
        cooldown_minutes=240,
        enabled=True,
    )
    assert (
        AlertRepository(database).save_user_rule("other-user", "rule-1", payload)
        is None
    )


def test_rule_payload_normalizes_strategy_codes():
    payload = validate_rule_payload(
        {
            "name": " Entry ",
            "conditions": {
                "strategy_codes": [" call_entry_setup ", "CALL_ENTRY_SETUP"]
            },
        }
    )
    assert payload["name"] == "Entry"
    assert payload["conditions"]["strategy_codes"] == ["CALL_ENTRY_SETUP"]
    assert payload["conditions"]["min_composite_score"] == 0.75


@pytest.mark.parametrize(
    "payload",
    [
        {"name": " "},
        {"name": "Entry", "user_id": "other-user"},
        {"name": "Entry", "cooldown_minutes": -1},
        {"name": "Entry", "conditions": {"min_composite_score": 75}},
        {"name": "Entry", "conditions": {"max_conflict_score": float("nan")}},
        {"name": "Entry", "conditions": {"min_composite_score": 10**1000}},
        {"name": "Entry", "conditions": {"min_strategy_count": 0}},
        {"name": "Entry", "conditions": {"min_strategy_count": 2147483648}},
        {"name": "Entry", "conditions": {"strategy_codes": [" "]}},
        {"name": "Entry", "conditions": {"strategy_code": ["MISSPELLED"]}},
    ],
)
def test_invalid_rule_payload_rejected(payload):
    with pytest.raises(ValueError):
        validate_rule_payload(payload)
