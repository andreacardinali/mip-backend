from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date

from market_analysis.models import FinancialSnapshot

SUPPORTED_METRICS = frozenset(
    {
        "ROE",
        "ROIC",
        "REVENUE_GROWTH",
        "EPS_GROWTH",
        "FREE_CASH_FLOW_MARGIN",
        "NET_DEBT_EBITDA",
        "PE_RATIO",
        "EV_EBITDA",
        "PRICE_FCF",
    }
)


def normalize_financial_metrics(
    rows: Iterable[Mapping[str, object]],
) -> FinancialSnapshot:
    metrics: dict[str, float] = {}
    latest_date: str | None = None

    for row in rows:
        name = str(row.get("metric_name") or "").strip().upper()
        value = row.get("metric_value")
        if name not in SUPPORTED_METRICS or value is None:
            continue
        metrics[name] = float(value)

        metric_date = row.get("metric_date")
        if isinstance(metric_date, date):
            candidate = metric_date.isoformat()
        elif metric_date:
            candidate = str(metric_date)
        else:
            candidate = None
        if candidate and (latest_date is None or candidate > latest_date):
            latest_date = candidate

    return FinancialSnapshot(metrics=metrics, metric_date=latest_date)
