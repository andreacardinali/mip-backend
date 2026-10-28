from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError

from routers import portfolios, positions, watchlist_items

USER_ID = "00000000-0000-0000-0000-000000000001"
JOB_ID = "00000000-0000-0000-0000-000000000002"
RUN_ID = "00000000-0000-0000-0000-000000000003"


def test_portfolio_list_batches_pricing_metadata(monkeypatch):
    db = MagicMock()
    identifiers = [UUID(USER_ID), UUID(JOB_ID)]
    results = [
        SimpleNamespace(_mapping={"portfolio_id": identifier, "portfolio_value": 100})
        for identifier in identifiers
    ]
    metadata = MagicMock()
    metadata.mappings.return_value = [
        {
            "portfolio_id": identifier,
            "total_position_count": 2,
            "pending_price_count": 0,
            "price_refresh_pending": False,
        }
        for identifier in identifiers
    ]
    db.execute.side_effect = [results, metadata]
    monkeypatch.setattr(portfolios, "SessionLocal", lambda: db)
    response = portfolios.get_portfolios(current_user={"user_id": USER_ID})
    assert db.execute.call_count == 2
    assert len(response) == 2
    assert all(row["pricing_complete"] for row in response)
    db.close.assert_called_once()


@pytest.mark.parametrize("amount", ["NaN", "Infinity", -1, "invalid"])
def test_invalid_historical_cost_is_rejected_before_database_access(
    monkeypatch, amount
):
    session_factory = MagicMock()
    monkeypatch.setattr(positions, "SessionLocal", session_factory)
    with pytest.raises(HTTPException) as raised:
        positions.upsert_position(
            {"instrument_id": JOB_ID, "cost_basis_amount": amount},
            current_user={"user_id": USER_ID},
        )
    assert raised.value.status_code == 422
    session_factory.assert_not_called()


def test_inaccessible_watchlist_item_returns_404_and_rolls_back(monkeypatch):
    class Denied(Exception):
        sqlstate = "23503"

    db = MagicMock()
    db.execute.side_effect = DBAPIError(None, None, Denied("inaccessible item"))
    monkeypatch.setattr(watchlist_items, "SessionLocal", lambda: db)
    with pytest.raises(HTTPException) as raised:
        watchlist_items.upsert_watchlist_item(
            {"watchlist_id": JOB_ID, "entity_id": RUN_ID},
            current_user={"user_id": USER_ID},
        )
    assert raised.value.status_code == 404
    db.rollback.assert_called_once()
    db.commit.assert_not_called()
    db.close.assert_called_once()


def test_scheduler_contended_manual_run_is_deferred_and_releases_connection(
    monkeypatch,
):
    import scheduler

    db = MagicMock()
    repository = MagicMock()
    connection = MagicMock()
    connection.execute.return_value.scalar_one.return_value = False
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: db)
    monkeypatch.setattr(scheduler, "SchedulerRepository", lambda session: repository)
    monkeypatch.setattr(
        scheduler, "engine", SimpleNamespace(connect=lambda: connection)
    )
    result = scheduler.execute_job(JOB_ID, "unused.module", RUN_ID, True)
    assert result == {"status": "SKIPPED", "reason": "already_running"}
    repository.defer_manual_request.assert_called_once_with(UUID(RUN_ID))
    repository.start_run.assert_not_called()
    db.commit.assert_called_once()
    db.close.assert_called_once()
    connection.close.assert_called_once()
