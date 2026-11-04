from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text

from job_runtime.database import run_transactional
from notifications.queue import NotificationQueue
from scheduler_triggers import SCHEDULER_TIMEZONE

_SUBSCRIPTIONS = text(
    """
    SELECT subscription.user_id, subscription.collection_id AS portfolio_id,
           subscription.notification_source_id, portfolio.name AS portfolio_name
    FROM public.collection_intelligence_subscriptions subscription
    JOIN public.portfolios portfolio
      ON portfolio.portfolio_id = subscription.collection_id
     AND portfolio.user_id = subscription.user_id
    JOIN public.users account ON account.user_id = subscription.user_id
    WHERE subscription.collection_type = 'PORTFOLIO'
      AND 'DAILY_PORTFOLIO_REPORT' = ANY(subscription.enabled_categories)
      AND account.is_active
    ORDER BY subscription.user_id, subscription.collection_id
    """
)
_PORTFOLIO_SUMMARY = text(
    "SELECT * FROM public.sp_get_portfolio_summary(:portfolio_id, :user_id)"
)


def _localized_number(value, *, signed: bool = False) -> str:
    format_spec = "+,.2f" if signed else ",.2f"
    whole, fraction = format(value, format_spec).split(".")
    return f"{whole.replace(',', '.')},{fraction}"


def _money(value, currency: str, *, signed: bool = False) -> str:
    if value is None:
        return "N/D"
    return f"{_localized_number(value, signed=signed)} {currency}"


def format_portfolio_report(summary, report_date: date) -> tuple[str, str, dict]:
    values = dict(summary)
    currency = values["reporting_currency"]
    profit_loss = values["profit_loss_reporting"]
    profit_loss_pct = values["profit_loss_pct"]
    percentage = (
        "N/D"
        if profit_loss_pct is None
        else f"{_localized_number(profit_loss_pct, signed=True)}%"
    )
    message = "\n".join(
        (
            f"Valore attuale: {_money(values['portfolio_value_reporting'], currency)}",
            f"Investito: {_money(values['invested_value_reporting'], currency)}",
            f"Risultato: {_money(profit_loss, currency, signed=True)} ({percentage})",
            f"Posizioni: {values['positions_count']}",
        )
    )
    context = {
        "reportDate": report_date.isoformat(),
        "portfolioCurrency": values["portfolio_currency"],
        "reportingCurrency": currency,
        "investedValue": (
            None
            if values["invested_value_reporting"] is None
            else str(values["invested_value_reporting"])
        ),
        "portfolioValue": (
            None
            if values["portfolio_value_reporting"] is None
            else str(values["portfolio_value_reporting"])
        ),
        "profitLoss": None if profit_loss is None else str(profit_loss),
        "profitLossPct": None if profit_loss_pct is None else str(profit_loss_pct),
        "positionsCount": values["positions_count"],
    }
    return message, currency, context


def publish_daily_portfolio_reports(db, queue, report_date: date) -> dict:
    subscriptions = db.execute(_SUBSCRIPTIONS).mappings().all()
    queued = 0
    skipped = 0
    for subscription in subscriptions:
        parameters = {
            "portfolio_id": subscription["portfolio_id"],
            "user_id": subscription["user_id"],
        }
        summary = db.execute(_PORTFOLIO_SUMMARY, parameters).mappings().first()
        if summary is None:
            skipped += 1
            continue

        message, currency, context = format_portfolio_report(summary, report_date)
        portfolio_id = str(subscription["portfolio_id"])
        user_id = str(subscription["user_id"])
        context.update(
            {
                "portfolioId": portfolio_id,
                "portfolioName": subscription["portfolio_name"],
            }
        )
        result = queue.enqueue(
            user_id=user_id,
            notification_key=f"DAILY_PORTFOLIO_REPORT|{portfolio_id}|{report_date.isoformat()}",
            notification_type="DAILY_PORTFOLIO_REPORT",
            entity_id=None,
            symbol=None,
            event_key=report_date.isoformat(),
            severity="LOW",
            title=f"Riepilogo giornaliero: {subscription['portfolio_name']}",
            message=message,
            context=context,
            source_id=(
                str(subscription["notification_source_id"])
                if subscription["notification_source_id"]
                else None
            ),
        )
        queued += bool(result)

    return {"processed": len(subscriptions), "queued": queued, "skipped": skipped}


def run() -> dict:
    report_date = datetime.now(ZoneInfo(SCHEDULER_TIMEZONE)).date()

    def operation(db):
        queue = NotificationQueue(db)
        return publish_daily_portfolio_reports(db, queue, report_date)

    return run_transactional(operation)