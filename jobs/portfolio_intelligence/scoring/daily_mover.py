def daily_mover_severity(
    change_pct: float,
    allocation_pct: float,
) -> str:
    score = abs(change_pct) + allocation_pct / 10.0
    if score >= 12:
        return "CRITICAL"
    if score >= 8:
        return "HIGH"
    if score >= 5:
        return "MEDIUM"
    return "LOW"
