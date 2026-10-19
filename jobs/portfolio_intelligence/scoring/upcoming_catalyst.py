def upcoming_catalyst_severity(
    days_until_event: int,
    allocation_pct: float,
) -> tuple[str, int]:
    time_score = 3 if days_until_event <= 1 else 2 if days_until_event <= 3 else 1
    weight_score = 3 if allocation_pct >= 20 else 2 if allocation_pct >= 5 else 1
    score = time_score + weight_score
    if score >= 6:
        return "CRITICAL", score
    if score >= 5:
        return "HIGH", score
    if score >= 4:
        return "MEDIUM", score
    return "LOW", score
