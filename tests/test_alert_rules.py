import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.exc import IntegrityError

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


def test_list_events_uses_owner_scoped_routine_and_pagination():
    database = Mock()
    database.execute.return_value.scalars.return_value = [
        {"alert_event_id": "event-1", "signal_id": None, "notification_status": None}
    ]
    result = AlertRepository(database).list_user_events(
        "owner", "rule-1", limit=26, offset=25
    )
    statement, parameters = database.execute.call_args.args
    assert "sp_alert_list_user_events" in str(statement)
    assert parameters == {
        "user_id": "owner",
        "rule_id": "rule-1",
        "limit": 26,
        "offset": 25,
    }
    assert result[0]["signal_id"] is None
    assert result[0]["notification_status"] is None


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


@pytest.mark.parametrize("enabled", [True, False])
def test_set_rule_enabled_uses_owner_scoped_routine(enabled):
    database = Mock()
    database.execute.return_value.scalar.return_value = True
    assert AlertRepository(database).set_user_rule_enabled("owner", "rule", enabled)
    statement, parameters = database.execute.call_args.args
    assert "sp_alert_set_rule_enabled" in str(statement)
    assert parameters == {"user_id": "owner", "rule_id": "rule", "enabled": enabled}


@pytest.mark.parametrize("found", [True, False])
def test_delete_rule_uses_owner_scoped_routine(found):
    database = Mock()
    database.execute.return_value.scalar.return_value = found
    assert AlertRepository(database).delete_user_rule("owner", "rule") is found
    statement, parameters = database.execute.call_args.args
    assert "sp_alert_delete_rule" in str(statement)
    assert parameters == {"user_id": "owner", "rule_id": "rule"}


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


def test_instrument_override_is_validated_and_normalized():
    instrument = "00000000-0000-0000-0000-000000000001"
    payload = validate_rule_payload({
        "name": "Watchlist", "conditions": {
            "notification_mode": "DIGEST", "regular_session_only": True,
            "instrument_overrides": {instrument: {
                "strategy_codes": [" supertrend "], "min_composite_score": 0.9,
            }},
        },
    })
    override = payload["conditions"]["instrument_overrides"][instrument]
    assert override["strategy_codes"] == ["SUPERTREND"]
    assert override["min_composite_score"] == 0.9
    with pytest.raises(ValueError):
        validate_rule_payload({"name": "Bad", "conditions": {
            "instrument_overrides": {instrument: {"enabled": "false"}},
        }})


@pytest.mark.parametrize("alert_type", ["FIXED_PRICE", "PERCENT_MOVE", "SIGNAL_CHANGE"])
def test_monitoring_rule_scopes_and_types(alert_type):
    conditions = {
        "scope": "PORTFOLIO",
        "scope_id": "00000000-0000-0000-0000-000000000001",
    }
    if alert_type != "SIGNAL_CHANGE":
        conditions.update(threshold=5, price_direction="DOWN")
    payload = validate_rule_payload(
        {"name": "Monitor", "alert_type": alert_type, "conditions": conditions}
    )
    assert payload["alert_type"] == alert_type
    assert payload["conditions"]["scope"] == "PORTFOLIO"
    database = Mock()
    AlertRepository(database).save_user_rule("owner", None, payload)
    assert database.execute.call_args.args[1]["alert_type"] == alert_type


@pytest.mark.parametrize(
    "conditions",
    [
        {"threshold": 0},
        {"threshold": float("nan")},
        {"threshold": 10**1000},
        {"threshold": 5, "price_direction": "SIDEWAYS"},
        {"threshold": 5, "scope": "PRIVATE"},
        {"threshold": 5, "scope": "PORTFOLIO", "scope_id": "invalid"},
        {"threshold": 5, "max_age_minutes": 0},
    ],
)
def test_invalid_price_monitoring_rejected(conditions):
    with pytest.raises(ValueError):
        validate_rule_payload(
            {"name": "Monitor", "alert_type": "FIXED_PRICE", "conditions": conditions}
        )


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


@pytest.fixture
def alert_api():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    owner = {"user_id": "00000000-0000-0000-0000-000000000001"}
    authentication = {"user": owner}

    def get_current_user():
        if authentication["user"] is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return authentication["user"]

    database = Mock()
    repository = Mock()
    repository.save_user_rule.return_value = "00000000-0000-0000-0000-000000000002"
    security = SimpleNamespace(CurrentUser=dict, get_current_user=get_current_user)
    with patch.dict(
        sys.modules,
        {
            "db": SimpleNamespace(SessionLocal=Mock(return_value=database)),
            "auth.security": security,
            "alerts.repository": SimpleNamespace(
                AlertRepository=Mock(return_value=repository)
            ),
        },
    ):
        namespace = runpy.run_path(
            str(Path(__file__).resolve().parents[1] / "routers" / "alerts.py")
        )
    app = FastAPI()
    app.include_router(namespace["router"], prefix="/api/alerts")
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, namespace["SessionLocal"], database, repository, authentication


@pytest.mark.parametrize("action", ["enabled", "delete"])
@pytest.mark.parametrize("outcome", ["success", "missing", "error"])
def test_rule_management_api_owner_and_transaction(alert_api, action, outcome):
    client, _, database, repository, authentication = alert_api
    rule_id = "00000000-0000-0000-0000-000000000002"
    method = (
        repository.set_user_rule_enabled
        if action == "enabled"
        else repository.delete_user_rule
    )
    method.return_value = outcome == "success"
    if outcome == "error":
        method.side_effect = RuntimeError("database error")
    response = (
        client.patch(f"/api/alerts/{rule_id}/enabled", json={"enabled": False})
        if action == "enabled"
        else client.delete(f"/api/alerts/{rule_id}")
    )
    assert (
        response.status_code == {"success": 204, "missing": 404, "error": 500}[outcome]
    )
    arguments = (authentication["user"]["user_id"], rule_id)
    if action == "enabled":
        arguments += (False,)
    method.assert_called_once_with(*arguments)
    if outcome == "success":
        assert response.content == b""
        database.commit.assert_called_once_with()
        database.rollback.assert_not_called()
    else:
        database.commit.assert_not_called()
        database.rollback.assert_called_once_with()
    database.close.assert_called_once_with()


@pytest.mark.parametrize("action", ["enabled", "delete"])
def test_rule_management_requires_authentication(alert_api, action):
    client, session_factory, _, _, authentication = alert_api
    authentication["user"] = None
    rule_id = "00000000-0000-0000-0000-000000000002"
    response = (
        client.patch(f"/api/alerts/{rule_id}/enabled", json={"enabled": True})
        if action == "enabled"
        else client.delete(f"/api/alerts/{rule_id}")
    )
    assert response.status_code == 401
    session_factory.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"enabled": None},
        {"enabled": "false"},
        {"enabled": 0},
        {"enabled": True, "user_id": "other"},
    ],
)
def test_rule_enabled_rejects_invalid_payload(alert_api, payload):
    client, session_factory, _, _, _ = alert_api
    response = client.patch(
        "/api/alerts/00000000-0000-0000-0000-000000000002/enabled", json=payload
    )
    assert response.status_code == 422
    session_factory.assert_not_called()


def test_alert_api_lists_events_for_authenticated_owner(alert_api):
    client, _, database, repository, authentication = alert_api
    rule_id = "00000000-0000-0000-0000-000000000002"
    repository.list_user_events.return_value = [{"alert_event_id": "event-1"}]
    response = client.get(f"/api/alerts/{rule_id}/events?limit=26&offset=25")
    assert response.status_code == 200
    assert response.json() == [{"alert_event_id": "event-1"}]
    repository.list_user_events.assert_called_once_with(
        authentication["user"]["user_id"], rule_id, limit=26, offset=25
    )
    database.close.assert_called_once_with()
    database.commit.assert_not_called()


@pytest.mark.parametrize(
    "query", ["limit=0", "limit=101", "offset=-1", "offset=100001"]
)
def test_alert_event_api_rejects_invalid_pagination(alert_api, query):
    client, session_factory, _, repository, _ = alert_api
    response = client.get(
        f"/api/alerts/00000000-0000-0000-0000-000000000002/events?{query}"
    )
    assert response.status_code == 422
    session_factory.assert_not_called()
    repository.list_user_events.assert_not_called()


def test_alert_event_api_requires_authentication(alert_api):
    client, session_factory, _, _, authentication = alert_api
    authentication["user"] = None
    response = client.get("/api/alerts/00000000-0000-0000-0000-000000000002/events")
    assert response.status_code == 401
    session_factory.assert_not_called()


def test_alert_event_api_closes_session_on_failure(alert_api):
    client, _, database, repository, _ = alert_api
    repository.list_user_events.side_effect = RuntimeError("database error")
    response = client.get("/api/alerts/00000000-0000-0000-0000-000000000002/events")
    assert response.status_code == 500
    database.close.assert_called_once_with()


def test_alert_api_lists_only_authenticated_owner(alert_api):
    client, session_factory, database, repository, authentication = alert_api
    repository.list_user_rules.return_value = [
        {"rule_id": "rule-1", "name": "Entry", "enabled": False}
    ]
    response = client.get("/api/alerts")
    assert response.status_code == 200
    assert response.json()[0]["enabled"] is False
    repository.list_user_rules.assert_called_once_with(
        authentication["user"]["user_id"]
    )
    session_factory.assert_called_once_with()
    database.close.assert_called_once_with()
    database.commit.assert_not_called()


@pytest.mark.parametrize("method", ["get", "post", "put"])
def test_alert_api_requires_authentication(alert_api, method):
    client, session_factory, _, _, authentication = alert_api
    authentication["user"] = None
    url = "/api/alerts"
    if method == "put":
        url += "/00000000-0000-0000-0000-000000000002"
    options = {} if method == "get" else {"json": {"name": "Entry"}}
    assert client.request(method, url, **options).status_code == 401
    session_factory.assert_not_called()


@pytest.mark.parametrize("method", ["post", "put"])
def test_alert_api_saves_normalized_rule_for_authenticated_owner(alert_api, method):
    client, _, database, repository, authentication = alert_api
    rule_id = repository.save_user_rule.return_value
    url = "/api/alerts" if method == "post" else f"/api/alerts/{rule_id}"
    payload = {
        "name": " Entry ",
        "conditions": {"strategy_codes": [" call_entry_setup "]},
    }
    response = client.request(method, url, json=payload)
    assert response.status_code == 200
    assert response.json() == {"rule_id": rule_id}
    repository.save_user_rule.assert_called_once_with(
        authentication["user"]["user_id"],
        None if method == "post" else rule_id,
        validate_rule_payload(payload),
    )
    database.commit.assert_called_once_with()
    database.rollback.assert_not_called()
    database.close.assert_called_once_with()


@pytest.mark.parametrize(
    ("method", "url", "payload"),
    [
        ("post", "/api/alerts", {"name": "Entry", "user_id": "other-user"}),
        ("post", "/api/alerts", {"name": " "}),
        ("post", "/api/alerts", {"name": "Entry", "cooldown_minutes": -1}),
        ("post", "/api/alerts", []),
        ("put", "/api/alerts/not-a-uuid", {"name": "Entry"}),
    ],
)
def test_alert_api_rejects_invalid_input_before_opening_session(
    alert_api, method, url, payload
):
    client, session_factory, _, repository, _ = alert_api
    assert client.request(method, url, json=payload).status_code == 422
    session_factory.assert_not_called()
    repository.save_user_rule.assert_not_called()


def test_alert_api_missing_or_foreign_rule_rolls_back(alert_api):
    client, _, database, repository, _ = alert_api
    repository.save_user_rule.return_value = None
    response = client.put(
        "/api/alerts/00000000-0000-0000-0000-000000000002",
        json={"name": "Entry"},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Alert rule not found"}
    database.commit.assert_not_called()
    database.rollback.assert_called_once_with()
    database.close.assert_called_once_with()


@pytest.mark.parametrize("sqlstate", ["23505", "23503", None])
def test_alert_api_database_errors_roll_back(alert_api, sqlstate):
    client, _, database, repository, _ = alert_api
    original = Exception("database error")
    original.sqlstate = sqlstate
    repository.save_user_rule.side_effect = IntegrityError("statement", {}, original)
    response = client.post("/api/alerts", json={"name": "Entry"})
    assert response.status_code == (409 if sqlstate == "23505" else 500)
    if sqlstate == "23505":
        assert response.json() == {"detail": "Alert rule name already exists"}
    database.commit.assert_not_called()
    database.rollback.assert_called_once_with()
    database.close.assert_called_once_with()


def test_alert_api_commit_failure_rolls_back(alert_api):
    client, _, database, _, _ = alert_api
    database.commit.side_effect = RuntimeError("commit failed")
    response = client.post("/api/alerts", json={"name": "Entry"})
    assert response.status_code == 500
    database.commit.assert_called_once_with()
    database.rollback.assert_called_once_with()
    database.close.assert_called_once_with()
