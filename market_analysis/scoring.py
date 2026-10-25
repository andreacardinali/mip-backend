from __future__ import annotations

from collections.abc import Mapping

from market_analysis.models import FinancialSnapshot, ScoreBreakdown, TechnicalSnapshot


def clamp_score(value: float) -> float:
    return max(0.0, min(100.0, value))


def score_technical_snapshot(snapshot: TechnicalSnapshot) -> float:
    score = 50.0

    if snapshot.sma_50 is not None:
        score += 10.0 if snapshot.market_price >= snapshot.sma_50 else -10.0
    if snapshot.sma_200 is not None:
        score += 15.0 if snapshot.market_price >= snapshot.sma_200 else -15.0
    if snapshot.rsi is not None:
        if 45.0 <= snapshot.rsi <= 60.0:
            score += 10.0
        elif snapshot.rsi >= 75.0 or snapshot.rsi <= 25.0:
            score -= 10.0
    if snapshot.daily_change_pct >= 2.0:
        score += 5.0
    elif snapshot.daily_change_pct <= -2.0:
        score -= 5.0

    return clamp_score(score)


def score_financial_snapshot(snapshot: FinancialSnapshot) -> float | None:
    contributions: list[float] = []

    roe = snapshot.get("ROE")
    if roe is not None:
        contributions.append(clamp_score(50.0 + roe * 2.0))

    revenue_growth = snapshot.get("REVENUE_GROWTH")
    if revenue_growth is not None:
        contributions.append(clamp_score(50.0 + revenue_growth * 2.0))

    net_debt_ebitda = snapshot.get("NET_DEBT_EBITDA")
    if net_debt_ebitda is not None:
        contributions.append(clamp_score(80.0 - net_debt_ebitda * 15.0))

    if not contributions:
        return None
    return sum(contributions) / len(contributions)


def weighted_composite_score(
    values: Mapping[str, float | None],
    weights: Mapping[str, float],
) -> float | None:
    present = {
        key: value
        for key, value in values.items()
        if value is not None and weights.get(key, 0.0) > 0
    }
    total_weight = sum(weights[key] for key in present)
    if total_weight <= 0:
        return None
    return clamp_score(
        sum(float(value) * weights[key] for key, value in present.items())
        / total_weight
    )


def build_score_breakdown(
    *,
    technical: float | None,
    financial: float | None = None,
    catalyst: float | None = None,
    llm: float | None = None,
    weights: Mapping[str, float] | None = None,
) -> ScoreBreakdown:
    effective_weights = weights or {
        "technical": 0.50,
        "financial": 0.25,
        "catalyst": 0.15,
        "llm": 0.10,
    }
    composite = weighted_composite_score(
        {
            "technical": technical,
            "financial": financial,
            "catalyst": catalyst,
            "llm": llm,
        },
        effective_weights,
    )
    return ScoreBreakdown(
        technical=technical,
        financial=financial,
        catalyst=catalyst,
        llm=llm,
        composite=composite,
    )
