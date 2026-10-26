from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any


def _timeframe(row):
    return str(
        row.get("signal_timeframe")
        or row.get("timeframe")
        or (row.get("evidence") or {}).get("timeframe")
        or "1D"
    )


def _strategy(row):
    return str(
        row.get("strategy_code")
        or (row.get("evidence") or {}).get("strategy_code")
        or "UNKNOWN"
    )


def build_strategy_consensus(rows: Iterable[Mapping[str, Any]]):
    groups = defaultdict(list)
    for row in rows:
        groups[(str(row["instrument_id"]), _timeframe(row))].append(dict(row))
    enriched = []
    for values in groups.values():
        longs = [x for x in values if str(x.get("direction")) == "LONG"]
        shorts = [x for x in values if str(x.get("direction")) == "SHORT"]
        total = len(values)
        majority = max(len(longs), len(shorts))
        minority = min(len(longs), len(shorts))
        if longs and shorts:
            direction = "CONFLICT"
        elif longs:
            direction = "LONG"
        elif shorts:
            direction = "SHORT"
        else:
            direction = "NEUTRAL"
        consensus = round((majority / total) * 100.0, 4) if total else 0.0
        conflict = round((2.0 * minority / total) * 100.0, 4) if total else 0.0
        primary = max(
            values,
            key=lambda x: (
                float(x.get("quant_score") or 0),
                float(x.get("technical_score") or 0),
                str(x.get("strategy_code") or ""),
            ),
        )
        primary_direction = str(primary.get("direction") or "")
        supporting = sorted(
            {
                _strategy(x)
                for x in values
                if str(x.get("direction")) == primary_direction
            }
        )
        opposing = sorted(
            {
                _strategy(x)
                for x in values
                if str(x.get("direction")) not in {"", primary_direction}
            }
        )
        row = dict(primary)
        row.update(
            {
                "strategy_count": total,
                "bullish_strategy_count": len(longs),
                "bearish_strategy_count": len(shorts),
                "consensus_direction": direction,
                "consensus_score": consensus,
                "conflict_score": conflict,
                "supporting_strategies": supporting,
                "opposing_strategies": opposing,
                "supporting_signal_ids": [
                    str(x["signal_id"])
                    for x in values
                    if str(x.get("direction")) == primary_direction
                ],
            }
        )
        enriched.append(row)
    return enriched
