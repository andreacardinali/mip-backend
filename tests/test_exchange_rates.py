from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services.exchange_rates import refresh_exchange_rates


@pytest.mark.parametrize("failure_at", ["history", "commit"])
def test_refresh_commits_each_currency_and_continues_after_failure(failure_at):
    events = []
    db = MagicMock()
    current_currency = None

    def execute(statement, params):
        nonlocal current_currency
        current_currency = params["currency_code"]
        operation = "history" if "reference_date" in params else "current"
        events.append((current_currency, operation))
        if current_currency == "BAD" and failure_at == operation:
            raise RuntimeError("history write failed")

    def commit():
        if current_currency == "BAD" and failure_at == "commit":
            raise RuntimeError("commit failed")
        events.append((current_currency, "commit"))

    db.execute.side_effect = execute
    db.commit.side_effect = commit
    db.rollback.side_effect = lambda: events.append((current_currency, "rollback"))
    service = MagicMock()
    service.get_exchange_rates.return_value = SimpleNamespace(
        source="ECB",
        reference_date=date(2026, 10, 20),
        base_currency="EUR",
        rates_to_usd={currency: Decimal("1") for currency in ("USD", "BAD", "EUR")},
    )

    result = refresh_exchange_rates(db, market_data_service=service)

    assert result["status"] == "PARTIAL"
    assert result["updated"] == 2
    assert result["failed"] == 1
    assert events == [
        ("USD", "current"),
        ("USD", "history"),
        ("USD", "commit"),
        ("BAD", "current"),
        ("BAD", "history"),
        ("BAD", "rollback"),
        ("EUR", "current"),
        ("EUR", "history"),
        ("EUR", "commit"),
    ]
    service.get_exchange_rates.assert_called_once()
