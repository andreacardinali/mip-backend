import runpy
import sys
import unittest
from datetime import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import DBAPIError

from alerts.rule_configuration import validate_rule_payload
from notifications.diagnostics import NotificationDiagnostics
from notifications.models import EnqueueResult, EnqueueStatus, NotificationTarget
from notifications.queue import NotificationQueue


class FakeRepository:
    def __init__(self, source_id="source", notification_id="notification"):
        self.source_id = source_id
        self.notification_id = notification_id

    def resolve_source_id(self, **kwargs):
        return self.source_id

    def resolve_target(self, **kwargs):
        source_id = kwargs.get("source_id") or self.source_id
        return NotificationTarget(
            source_id,
            kwargs["fallback_channel"] if source_id else None,
            "RESOLVED" if source_id else "SOURCE_NOT_FOUND",
        )

    def enqueue(self, **kwargs):
        return self.notification_id


class EnqueueTests(unittest.TestCase):
    def test_pause_blocks_even_an_explicit_destination(self):
        queue = self._queue()
        queue.repo = MagicMock()
        queue.repo.resolve_target.return_value = NotificationTarget(
            None, None, "PAUSED"
        )
        result = self._enqueue(queue, source_id="destination")
        self.assertEqual(result.status, EnqueueStatus.DISABLED)
        self.assertEqual(
            queue.repo.resolve_target.call_args.kwargs["source_id"], "destination"
        )
        queue.repo.enqueue.assert_not_called()

    def test_destination_is_resolved_before_enqueue(self):
        queue = self._queue()
        queue.repo = MagicMock()
        queue.repo.resolve_target.return_value = NotificationTarget(
            "owned", "TELEGRAM", "RESOLVED"
        )
        queue.repo.enqueue.return_value = "notification"
        self.assertTrue(self._enqueue(queue, source_id="owned"))
        self.assertEqual(queue.repo.enqueue.call_args.kwargs["source_id"], "owned")
        self.assertEqual(queue.repo.resolve_target.call_args.kwargs["context"], {})

    def test_rule_destination_is_normalized_and_invalid_ids_are_rejected(self):
        payload = {
            "name": "Price alert",
            "conditions": {"source_id": "00000000-0000-0000-0000-000000000ABC"},
        }
        self.assertEqual(
            validate_rule_payload(payload)["conditions"]["source_id"],
            "00000000-0000-0000-0000-000000000abc",
        )
        for invalid in ("invalid", "", 1, True):
            with self.subTest(source_id=invalid), self.assertRaises(ValueError):
                validate_rule_payload(
                    {"name": "Alert", "conditions": {"source_id": invalid}}
                )
        self.assertNotIn(
            "source_id", validate_rule_payload({"name": "Alert"})["conditions"]
        )

    def _queue(
        self,
        *,
        enabled=True,
        source_id="source",
        notification_id="notification",
    ):
        queue = object.__new__(NotificationQueue)
        queue.enabled = enabled
        queue.default_channel = "TELEGRAM"
        queue.repo = FakeRepository(source_id, notification_id)
        return queue

    def _enqueue(self, queue, **kwargs):
        return queue.enqueue(
            user_id="user",
            notification_key="key",
            notification_type="TEST",
            entity_id=None,
            symbol=None,
            event_key=None,
            severity="LOW",
            title="Title",
            message="Message",
            context={},
            **kwargs,
        )

    def test_created(self):
        result = self._enqueue(self._queue())
        self.assertEqual(result.status, EnqueueStatus.CREATED)
        self.assertTrue(result)

    def test_disabled(self):
        result = self._enqueue(self._queue(enabled=False))
        self.assertEqual(result.status, EnqueueStatus.DISABLED)
        self.assertFalse(result)

    def test_source_not_found(self):
        result = self._enqueue(self._queue(source_id=None))
        self.assertEqual(result.status, EnqueueStatus.SOURCE_NOT_FOUND)

    def test_duplicate(self):
        result = self._enqueue(self._queue(notification_id=None))
        self.assertEqual(result.status, EnqueueStatus.DUPLICATE)

    def test_diagnostics_counts_enqueue_outcomes(self):
        diagnostics = NotificationDiagnostics()
        outcomes = (
            EnqueueResult(EnqueueStatus.CREATED, "notification-1"),
            EnqueueResult(EnqueueStatus.DUPLICATE),
            EnqueueResult(EnqueueStatus.DISABLED),
            EnqueueResult(EnqueueStatus.SOURCE_NOT_FOUND),
        )

        self.assertTrue(diagnostics.record(outcomes[0]))
        for outcome in outcomes[1:]:
            self.assertFalse(diagnostics.record(outcome))

        self.assertEqual(
            diagnostics.to_dict(),
            {
                "notificationsCreated": 1,
                "notificationsDuplicate": 1,
                "notificationsDisabled": 1,
                "notificationsWithoutSource": 1,
            },
        )


@pytest.fixture
def notification_api():
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    authentication = {"user": {"user_id": "00000000-0000-0000-0000-000000000001"}}

    def get_current_user():
        if authentication["user"] is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return authentication["user"]

    database = MagicMock()
    delivery = {"paused": False, "default_source_id": None}
    database.execute.return_value.all.return_value = []
    database.execute.return_value.one.return_value = SimpleNamespace(_mapping=delivery)
    session_factory = MagicMock(return_value=database)
    with patch.dict(
        sys.modules,
        {
            "db": SimpleNamespace(SessionLocal=session_factory),
            "auth.security": SimpleNamespace(
                CurrentUser=dict, get_current_user=get_current_user
            ),
        },
    ):
        namespace = runpy.run_path(
            str(
                Path(__file__).resolve().parents[1]
                / "routers"
                / "notification_preferences.py"
            )
        )
    app = FastAPI()
    app.include_router(namespace["router"], prefix="/api/notification-preferences")
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, database, session_factory, authentication


@pytest.mark.parametrize("paused", [True, False])
@pytest.mark.parametrize("source_id", [None, "00000000-0000-0000-0000-000000000ABC"])
def test_delivery_api_saves_authenticated_owner(notification_api, paused, source_id):
    client, database, _, authentication = notification_api
    response = client.put(
        "/api/notification-preferences/delivery",
        json={
            "paused": paused,
            "default_source_id": source_id,
            "daily_portfolio_report_time": "21:00",
        },
    )
    assert response.status_code == 200
    statement, parameters = database.execute.call_args_list[0].args
    assert "sp_save_user_notification_delivery" in str(statement)
    assert parameters == {
        "user_id": authentication["user"]["user_id"],
        "paused": paused,
        "source_id": source_id.lower() if source_id else None,
        "report_time": time(21, 0),
    }
    assert response.json()["delivery"] == {"paused": False, "default_source_id": None}
    database.commit.assert_called_once_with()
    database.rollback.assert_not_called()
    assert database.close.call_count == 2


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"paused": True},
        {
            "paused": "false",
            "default_source_id": None,
            "daily_portfolio_report_time": "21:00",
        },
        {
            "paused": 0,
            "default_source_id": None,
            "daily_portfolio_report_time": "21:00",
        },
        {
            "paused": False,
            "default_source_id": "invalid",
            "daily_portfolio_report_time": "21:00",
        },
        {
            "paused": False,
            "default_source_id": None,
            "daily_portfolio_report_time": "21:00",
            "user_id": "other",
        },
    ],
)
def test_delivery_api_rejects_invalid_payload_before_database(
    notification_api, payload
):
    client, _, session_factory, _ = notification_api
    assert (
        client.put("/api/notification-preferences/delivery", json=payload).status_code
        == 422
    )
    session_factory.assert_not_called()


@pytest.mark.parametrize("sqlstate, status", [("22023", 422), ("08006", 500)])
def test_delivery_api_rolls_back_database_errors(notification_api, sqlstate, status):
    client, database, _, _ = notification_api
    error = RuntimeError("Database error")
    error.sqlstate = sqlstate
    database.execute.side_effect = DBAPIError("save", {}, error)
    response = client.put(
        "/api/notification-preferences/delivery",
        json={
            "paused": False,
            "default_source_id": None,
            "daily_portfolio_report_time": "21:00",
        },
    )
    assert response.status_code == status
    database.commit.assert_not_called()
    database.rollback.assert_called_once_with()
    database.close.assert_called_once_with()


@pytest.mark.parametrize("enabled", [True, False])
def test_screener_summary_subscription_does_not_require_a_destination(
    notification_api, enabled
):
    client, database, _, authentication = notification_api
    response = client.put(
        "/api/notification-preferences/screener-summary", json={"enabled": enabled}
    )
    assert response.status_code == 200
    statement, parameters = database.execute.call_args_list[0].args
    assert "sp_save_user_screener_summary_subscription" in str(statement)
    assert parameters == {
        "user_id": authentication["user"]["user_id"],
        "enabled": enabled,
    }
    database.commit.assert_called_once_with()
    database.rollback.assert_not_called()


@pytest.mark.parametrize("endpoint", ["delivery", "screener-summary"])
def test_notification_configuration_requires_authentication(notification_api, endpoint):
    client, _, session_factory, authentication = notification_api
    authentication["user"] = None
    payload = (
        {"paused": False, "default_source_id": None}
        if endpoint == "delivery"
        else {"enabled": True}
    )
    assert (
        client.put(
            f"/api/notification-preferences/{endpoint}", json=payload
        ).status_code
        == 401
    )
    session_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
