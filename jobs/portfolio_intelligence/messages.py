from collections.abc import Mapping
from typing import Any


def entity_label(item: Mapping[str, Any]) -> str:
    symbol = item["symbol"]
    name = item.get("name")
    return f"{symbol} | {name}" if name else symbol


def collection_details(item: Mapping[str, Any]) -> str:
    memberships = item.get("collections") or []
    portfolio_exposure = item.get("portfolioExposure") or []
    if memberships:
        details = []
        for membership in memberships:
            collection_type = membership.get("collectionType") or "Collection"
            label = membership.get("collectionName") or membership.get("collectionId")
            line = f"{collection_type}: {label or 'N/D'}"
            if collection_type.upper() == "PORTFOLIO":
                exposure = next(
                    (
                        value
                        for value in portfolio_exposure
                        if value.get("collectionName") == membership.get("collectionName")
                    ),
                    {},
                )
                allocation = exposure.get("allocationPct")
                if allocation is not None:
                    line += f" (peso {allocation:.2f}%"
                    impact = exposure.get("portfolioImpactPct")
                    if impact is not None:
                        line += f"; contributo stimato {impact:+.3f} p.p."
                    line += ")"
                elif exposure:
                    line += " (peso non disponibile)"
            details.append(line)
        return "Presente in: " + "; ".join(details) + "\n"
    if not item.get("collectionId"):
        return ""
    details = f"Collection: {item.get('collectionName') or item['collectionId']}\n"
    if item.get("collectionType") == "PORTFOLIO":
        allocation = item.get("allocationPct")
        details += (
            f"Peso nel portafoglio: {allocation:.2f}%\n"
            if allocation is not None
            else "Peso nel portafoglio: non disponibile\n"
        )
        impact = item.get("portfolioImpactPct")
        if impact is not None:
            details += (
                f"Contributo giornaliero stimato: {impact:+.3f} punti percentuali\n"
            )
    return details


def format_daily_mover_message(item: Mapping[str, Any]) -> str:
    change = float(item["dailyChangePct"])
    sign = "+" if change > 0 else ""
    return (
        f"📈 DAILY MOVER\n{entity_label(item)} | {sign}{change:.2f}%\n"
        f"{collection_details(item)}"
        f"Severity: {item['severity']}\n{item.get('headline', '')}\n"
        f"{item.get('explanation', '')}\n"
        f"Verifica: {item.get('recommendedCheck', '')}"
    )


def format_upcoming_catalyst_message(item: Mapping[str, Any]) -> str:
    return (
        f"📅 UPCOMING CATALYST\n{entity_label(item)} | "
        f"{item.get('eventType')} | {item.get('eventDate')}\n"
        f"{collection_details(item)}"
        f"Severity: {item['severity']}\n{item.get('headline', '')}\n"
        f"{item.get('eventSummary', '')}"
    )


def format_breaking_catalyst_message(item: Mapping[str, Any]) -> str:
    return (
        f"⚡ BREAKING CATALYST\n{entity_label(item)} | {item.get('eventType')}\n"
        f"{collection_details(item)}"
        f"Severity: {item['severity']}\n{item.get('headline', '')}\n"
        f"{item.get('eventSummary', '')}\n{item.get('materialityReason', '')}"
    )
