from collections.abc import Mapping
from typing import Any


def format_daily_mover_message(item: Mapping[str, Any]) -> str:
    change = float(item["dailyChangePct"])
    sign = "+" if change > 0 else ""
    return (
        f"📈 DAILY MOVER\n{item['symbol']} | {sign}{change:.2f}%\n"
        f"Severity: {item['severity']}\n{item.get('headline', '')}\n"
        f"{item.get('explanation', '')}\n"
        f"Verifica: {item.get('recommendedCheck', '')}"
    )


def format_upcoming_catalyst_message(item: Mapping[str, Any]) -> str:
    return (
        f"📅 UPCOMING CATALYST\n{item['symbol']} | "
        f"{item.get('eventType')} | {item.get('eventDate')}\n"
        f"Severity: {item['severity']}\n{item.get('headline', '')}\n"
        f"{item.get('eventSummary', '')}"
    )


def format_breaking_catalyst_message(item: Mapping[str, Any]) -> str:
    return (
        f"⚡ BREAKING CATALYST\n{item['symbol']} | {item.get('eventType')}\n"
        f"Severity: {item['severity']}\n{item.get('headline', '')}\n"
        f"{item.get('eventSummary', '')}\n{item.get('materialityReason', '')}"
    )
