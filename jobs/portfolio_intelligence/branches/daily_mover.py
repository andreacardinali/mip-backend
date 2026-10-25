import json
from datetime import UTC, datetime

from jobs.portfolio_intelligence.evidence import (
    MarketEvidenceCollector,
    instrument_reference,
)
from jobs.portfolio_intelligence.messages import format_daily_mover_message
from jobs.portfolio_intelligence.models import BranchResult
from jobs.portfolio_intelligence.parsing import parse_daily_validation
from jobs.portfolio_intelligence.repositories.entities import load_candidates
from jobs.portfolio_intelligence.scoring import daily_mover_severity
from llm.base import LLMRequest
from llm.evidence import select_sources
from market_analysis.technical import analyze_price_bars
from market_data.models import HistoryRequest
from notifications.diagnostics import NotificationDiagnostics


class DailyMoverBranch:
    name = "DAILY_MOVER"

    def execute(self, context):
        result = BranchResult(branch=self.name)
        entities = load_candidates(context.db)
        result.candidate_count = len(entities)
        research_template = context.prompts.get("daily_research")
        validation_template = context.prompts.get("daily_validate")
        accepted = []
        collector = MarketEvidenceCollector(context.market_data, context.config)
        economic_events = collector.common_economic_events()

        for entity in entities:
            try:
                bars = context.market_data.get_ohlcv(
                    HistoryRequest(
                        instrument=instrument_reference(entity),
                        period=context.config.history_range,
                        interval=context.config.history_interval,
                    )
                )
            except Exception as exc:
                result.add_error(f"{entity.symbol}: price history unavailable: {exc}")
                continue
            if len(bars) < context.config.min_history_rows:
                continue
            snapshot = analyze_price_bars(bars)
            if (
                snapshot is None
                or abs(snapshot.daily_change_pct) < context.config.daily_move_min_pct
            ):
                continue

            observed = {
                **entity.to_dict(),
                **snapshot.to_dict(),
                "allocationPct": entity.allocation_pct,
                "portfolioImpactPct": snapshot.daily_change_pct
                * entity.allocation_pct
                / 100.0,
                "severity": daily_mover_severity(
                    snapshot.daily_change_pct, entity.allocation_pct
                ),
                "marketEvidence": collector.for_daily_mover(
                    entity, economic_events=economic_events
                ).to_dict(),
            }
            research = context.research_llm.generate_json(
                LLMRequest(
                    prompt=research_template.render(
                        observed=json.dumps(observed, default=str)
                    ),
                    grounded_search=True,
                    max_tokens=4096,
                )
            )
            final = context.validation_llm.generate_json(
                LLMRequest(
                    prompt=validation_template.render(
                        observed=json.dumps(observed, default=str),
                        catalyst=json.dumps(research.data.get("catalyst"), default=str),
                        supports=json.dumps(
                            research.data.get("supports") or research.sources,
                            default=str,
                        ),
                    ),
                    max_tokens=900,
                )
            )
            validation = parse_daily_validation(
                final.data, expected_symbol=entity.symbol
            )
            accepted.append(
                {
                    **observed,
                    **validation.payload,
                    "entity_id": entity.entity_id,
                    "sourcesUsed": select_sources(
                        validation.source_indices, research.sources
                    ),
                }
            )

        result.accepted_count = len(accepted)
        diagnostics = NotificationDiagnostics()
        for item in accepted:
            observed_day = (
                datetime.fromtimestamp(item["observedDate"], UTC).date().isoformat()
            )
            notification_key = (
                f"DAILY_MOVER|{item['symbol']}|{observed_day}|"
                f"{round(item['dailyChangePct'], 1)}"
            )
            enqueue_result = context.notification_queue.enqueue(
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
            )
            if diagnostics.record(enqueue_result):
                result.notification_count += 1
        result.diagnostics = {
            "entityErrors": len(result.errors),
            "marketEvidence": collector.diagnostics.to_dict(),
            **diagnostics.to_dict(),
        }
        return result


def run():
    from jobs.portfolio_intelligence.runtime import run_branch

    return run_branch(DailyMoverBranch())
