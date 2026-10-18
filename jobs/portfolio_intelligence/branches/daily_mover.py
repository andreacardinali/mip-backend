import json
from datetime import datetime, timezone
from pathlib import Path

from jobs.portfolio_intelligence.branches.base import IntelligenceBranch
from jobs.portfolio_intelligence.evidence.validator import validated_sources
from jobs.portfolio_intelligence.llm.base import LLMRequest
from jobs.portfolio_intelligence.market.indicators import analyze, severity
from jobs.portfolio_intelligence.market.yahoo import load_history
from jobs.portfolio_intelligence.models import BranchResult
from jobs.portfolio_intelligence.repositories.entities import load_candidates


class DailyMoverBranch(IntelligenceBranch):
    name = "DAILY_MOVER"

    def execute(self, context):
        result = BranchResult(branch=self.name)
        entities = load_candidates(context.db)
        result.candidate_count = len(entities)
        root = Path(__file__).parent.parent
        research_tpl = (root / "prompts/daily_research.txt").read_text()
        validate_tpl = (root / "prompts/daily_validate.txt").read_text()
        accepted = []

        for entity in entities:
            try:
                rows = load_history(
                    entity.symbol,
                    context.config.yahoo_range,
                    context.config.yahoo_interval,
                    context.config.market_data_timeout,
                )
                if len(rows) < context.config.min_history_rows:
                    continue
                tech = analyze(rows)
                if not tech or abs(tech["dailyChangePct"]) < context.config.daily_move_min_pct:
                    continue
                observed = {
                    **entity.to_dict(),
                    **tech,
                    "allocationPct": entity.allocation_pct,
                    "portfolioImpactPct": (
                        tech["dailyChangePct"] * entity.allocation_pct / 100
                    ),
                    "severity": severity(
                        tech["dailyChangePct"], entity.allocation_pct
                    ),
                }
                research = context.research_llm.generate_json(
                    LLMRequest(
                        prompt=research_tpl.format(
                            observed=json.dumps(observed, default=str)
                        ),
                        grounded_search=True,
                        max_tokens=4096,
                    )
                )
                final = context.validation_llm.generate_json(
                    LLMRequest(
                        prompt=validate_tpl.format(
                            observed=json.dumps(observed, default=str),
                            catalyst=json.dumps(
                                research.data.get("catalyst"), default=str
                            ),
                            supports=json.dumps(
                                research.data.get("supports") or research.sources,
                                default=str,
                            ),
                        ),
                        max_tokens=900,
                    )
                )
                data = final.data
                if data.get("symbol") != entity.symbol:
                    raise ValueError("Validation LLM symbol mismatch")
                cause = bool(data.get("causeFound"))
                indices = data.get("sourceIndicesUsed") or []
                if not cause:
                    data.update(
                        {
                            "causeConfidence": 0,
                            "likelyCause": None,
                            "sourceIndicesUsed": [],
                        }
                    )
                accepted.append(
                    {
                        **observed,
                        **data,
                        "entity_id": entity.entity_id,
                        "sourcesUsed": validated_sources(indices, research.sources),
                    }
                )
            except Exception as exc:
                result.errors.append(f"{entity.symbol}: {exc}")

        result.accepted_count = len(accepted)
        try:
            for item in accepted:
                observed_day = datetime.fromtimestamp(
                    item["observedDate"], timezone.utc
                ).date().isoformat()
                notification_key = (
                    f"DAILY_MOVER|{item['symbol']}|{observed_day}|"
                    f"{round(item['dailyChangePct'], 1)}"
                )
                sign = "+" if item["dailyChangePct"] > 0 else ""
                message = (
                    f"📈 DAILY MOVER\n{item['symbol']} | "
                    f"{sign}{item['dailyChangePct']:.2f}%\n"
                    f"Severity: {item['severity']}\n"
                    f"{item.get('headline', '')}\n"
                    f"{item.get('explanation', '')}\n"
                    f"Verifica: {item.get('recommendedCheck', '')}"
                )
                if context.notification_queue.enqueue(
                    user_id=item["user_id"],
                    notification_key=notification_key,
                    notification_type=self.name,
                    entity_id=item["entity_id"],
                    symbol=item["symbol"],
                    event_key=observed_day,
                    severity=item["severity"],
                    title=item.get("headline") or notification_key,
                    message=message,
                    context=item,
                ):
                    result.notification_count += 1
            context.db.commit()
        except Exception as exc:
            context.db.rollback()
            result.status = "FAILED"
            result.errors.append(str(exc))

        if result.errors and result.status == "SUCCESS":
            result.status = "PARTIAL"
        result.diagnostics = {"entityErrors": len(result.errors)}
        return result


def run():
    from jobs.portfolio_intelligence.runtime import run_branch
    return run_branch(DailyMoverBranch())
