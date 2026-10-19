import json
from datetime import datetime, timezone

from jobs.portfolio_intelligence.branches.base import IntelligenceBranch
from jobs.portfolio_intelligence.messages import format_daily_mover_message
from jobs.portfolio_intelligence.models import BranchResult
from jobs.portfolio_intelligence.repositories.entities import load_candidates
from jobs.portfolio_intelligence.scoring import daily_mover_severity
from llm.base import LLMRequest
from llm.evidence import select_sources
from market_analysis.technical import analyze_price_bars


class DailyMoverBranch(IntelligenceBranch):
    name = "DAILY_MOVER"

    def execute(self, context):
        result = BranchResult(branch=self.name)
        entities = load_candidates(context.db)
        result.candidate_count = len(entities)
        research_template = context.prompts.get("daily_research")
        validation_template = context.prompts.get("daily_validate")
        accepted = []

        for entity in entities:
            try:
                bars = context.market_data.get_history(
                    entity.symbol,
                    period=context.config.history_range,
                    interval=context.config.history_interval,
                )
                if len(bars) < context.config.min_history_rows:
                    continue
                snapshot = analyze_price_bars(bars)
                if snapshot is None or abs(snapshot.daily_change_pct) < context.config.daily_move_min_pct:
                    continue
                observed = {
                    **entity.to_dict(),
                    **snapshot.to_dict(),
                    "allocationPct": entity.allocation_pct,
                    "portfolioImpactPct": snapshot.daily_change_pct * entity.allocation_pct / 100.0,
                    "severity": daily_mover_severity(snapshot.daily_change_pct, entity.allocation_pct),
                }
                research = context.research_llm.generate_json(
                    LLMRequest(
                        prompt=research_template.render(observed=json.dumps(observed, default=str)),
                        grounded_search=True,
                        max_tokens=4096,
                    )
                )
                final = context.validation_llm.generate_json(
                    LLMRequest(
                        prompt=validation_template.render(
                            observed=json.dumps(observed, default=str),
                            catalyst=json.dumps(research.data.get("catalyst"), default=str),
                            supports=json.dumps(research.data.get("supports") or research.sources, default=str),
                        ),
                        max_tokens=900,
                    )
                )
                data = final.data
                if data.get("symbol") != entity.symbol:
                    raise ValueError("Validation LLM symbol mismatch")
                indices = data.get("sourceIndicesUsed") or []
                if not bool(data.get("causeFound")):
                    data.update({"causeConfidence": 0, "likelyCause": None, "sourceIndicesUsed": []})
                accepted.append({
                    **observed,
                    **data,
                    "entity_id": entity.entity_id,
                    "sourcesUsed": select_sources(indices, research.sources),
                })
            except Exception as exc:
                result.add_error(f"{entity.symbol}: {exc}")

        result.accepted_count = len(accepted)
        for item in accepted:
            observed_day = datetime.fromtimestamp(item["observedDate"], timezone.utc).date().isoformat()
            notification_key = f"DAILY_MOVER|{item['symbol']}|{observed_day}|{round(item['dailyChangePct'], 1)}"
            if context.notification_queue.enqueue(
                user_id=item["user_id"],
                notification_key=notification_key,
                notification_type=self.name,
                entity_id=item["entity_id"],
                symbol=item["symbol"],
                event_key=observed_day,
                severity=item["severity"],
                title=item.get("headline") or notification_key,
                message=format_daily_mover_message(item),
                context=item,
            ):
                result.notification_count += 1
        result.diagnostics = {"entityErrors": len(result.errors)}
        return result


def run():
    from jobs.portfolio_intelligence.runtime import run_branch
    return run_branch(DailyMoverBranch())
