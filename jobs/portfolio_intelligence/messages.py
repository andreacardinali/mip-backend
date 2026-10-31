from collections.abc import Mapping
from typing import Any


def collection_details(item: Mapping[str, Any]) -> str:
    if not item.get("collectionId"):
        return ""
    details = f"Collection: {item.get('collectionName') or item['collectionId']}\n"
    if item.get("collectionType") == "PORTFOLIO":
        allocation = item.get("allocationPct")
        details += (
            f"Peso nel portafoglio: {allocation:.2f}%\n"
            if allocation is not None else "Peso nel portafoglio: non disponibile\n"
        )
        impact = item.get("portfolioImpactPct")
        if impact is not None:
            details += f"Contributo giornaliero stimato: {impact:+.3f} punti percentuali\n"
    return details


def format_daily_mover_message(item: Mapping[str, Any]) -> str:
    change = float(item["dailyChangePct"])
    sign = "+" if change > 0 else ""
    return (
        f"📈 DAILY MOVER\n{item['symbol']} | {sign}{change:.2f}%\n"
        f"{collection_details(item)}"
        f"Severity: {item['severity']}\n{item.get('headline', '')}\n"
        f"{item.get('explanation', '')}\n"
        f"Verifica: {item.get('recommendedCheck', '')}"
    )


def format_upcoming_catalyst_message(item: Mapping[str, Any]) -> str:
    return (
        f"📅 UPCOMING CATALYST\n{item['symbol']} | "
        f"{item.get('eventType')} | {item.get('eventDate')}\n"
        f"{collection_details(item)}"
        f"Severity: {item['severity']}\n{item.get('headline', '')}\n"
        f"{item.get('eventSummary', '')}"
    )


def format_breaking_catalyst_message(item: Mapping[str, Any]) -> str:
    return (
        f"⚡ BREAKING CATALYST\n{item['symbol']} | {item.get('eventType')}\n"
        f"{collection_details(item)}"
        f"Severity: {item['severity']}\n{item.get('headline', '')}\n"
        f"{item.get('eventSummary', '')}\n{item.get('materialityReason', '')}"
    )
