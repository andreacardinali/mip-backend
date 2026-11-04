from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

from jobs.daily_portfolio_report.worker import (
    format_portfolio_report,
    publish_daily_portfolio_reports,
)


def test_format_portfolio_report_uses_reporting_currency_and_handles_unknown_cost():
    message, currency, context = format_portfolio_report(
        {
            "portfolio_currency": "USD",
            "reporting_currency": "EUR",
            "invested_value_reporting": None,
            "portfolio_value_reporting": Decimal("1234.5"),
            "profit_loss_reporting": None,
            "profit_loss_pct": None,
            "positions_count": 2,
        },
        date(2025, 10, 26),
    )

    assert currency == "EUR"
    assert message == (
        "Valore attuale: 1.234,50 EUR\n"
        "Investito: N/D\n"
        "Risultato: N/D (N/D)\n"
        "Posizioni: 2"
    )
    assert context["reportDate"] == "2025-10-26"
    assert context["portfolioCurrency"] == "USD"
    assert context["investedValue"] is None
    assert context["portfolioValue"] == "1234.5"
    assert context["profitLoss"] is None
    assert context["profitLossPct"] is None


def test_daily_portfolio_report_uses_selected_destination_and_stable_daily_key():
    report_date = date(2025, 10, 26)
    subscription = {
        "user_id": "user-id",
        "portfolio_id": "portfolio-id",
        "notification_source_id": "source-id",
        "portfolio_name": "Long-term",
    }
    summary = {
        "portfolio_currency": "USD",
        "reporting_currency": "EUR",
        "invested_value_reporting": Decimal("100.00"),
        "portfolio_value_reporting": Decimal("125.00"),
        "profit_loss_reporting": Decimal("25.00"),
        "profit_loss_pct": Decimal("25.00"),
        "positions_count": 1,
    }
    db = MagicMock()
    db.execute.side_effect = [
        MagicMock(mappings=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[subscription])))),
        MagicMock(mappings=MagicMock(return_value=MagicMock(first=MagicMock(return_value=summary)))),
    ]
    queue = MagicMock()
    queue.enqueue.return_value = True

    result = publish_daily_portfolio_reports(db, queue, report_date)

    assert result == {"processed": 1, "queued": 1, "skipped": 0}
    arguments = queue.enqueue.call_args.kwargs
    assert arguments["notification_type"] == "DAILY_PORTFOLIO_REPORT"
    assert arguments["notification_key"] == (
        "DAILY_PORTFOLIO_REPORT|portfolio-id|2025-10-26"
    )
    assert arguments["source_id"] == "source-id"
    assert arguments["entity_id"] is None
    assert arguments["event_key"] == "2025-10-26"
    assert "25,00 EUR (+25,00%)" in arguments["message"]