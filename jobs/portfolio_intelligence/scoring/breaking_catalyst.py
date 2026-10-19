def breaking_catalyst_severity(
    confidence: int,
    allocation_pct: float,
) -> str:
    if confidence >= 90 and allocation_pct >= 20:
        return "CRITICAL"
    if confidence >= 80:
        return "HIGH"
    return "MEDIUM"
