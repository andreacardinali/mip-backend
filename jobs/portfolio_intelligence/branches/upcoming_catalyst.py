import json
import logging
from datetime import date, timedelta

from job_runtime.collections import batched
from job_runtime.progress import BatchProgress
from jobs.portfolio_intelligence.batching import matched_batch_rows
from jobs.portfolio_intelligence.evidence import MarketEvidenceCollector
from jobs.portfolio_intelligence.incremental import IncrementalNotificationWriter
from jobs.portfolio_intelligence.messages import format_upcoming_catalyst_message
from jobs.portfolio_intelligence.models import BranchResult
from jobs.portfolio_intelligence.parsing import (
    PayloadValidationError,
    parse_upcoming_event,
)
from jobs.portfolio_intelligence.repositories.catalysts import recent_catalysts
from jobs.portfolio_intelligence.repositories.entities import load_candidates
from jobs.portfolio_intelligence.scoring import upcoming_catalyst_severity
from llm.base import LLMRequest

logger = logging.getLogger(__name__)


class UpcomingCatalystBranch:
    name = "UPCOMING_CATALYST"

    def execute(self, context):
        result = BranchResult(branch=self.name)
        candidates = load_candidates(context.db)
        result.candidate_count = len(candidates)
        today = date.today()
        end = today + timedelta(days=context.config.upcoming_window_days)
        known = recent_catalysts(
            context.db, [candidate.entity_id for candidate in candidates], 30
        )
        template = context.prompts.get("upcoming")
        collector = MarketEvidenceCollector(context.market_data, context.config)
        economic_events = collector.common_economic_events()
        writer = IncrementalNotificationWriter(context.notification_queue)
        progress = BatchProgress(
            job="portfolio_intelligence_upcoming_catalyst",
            total_items=len(candidates),
            max_items_per_batch=context.config.llm_request_max_candidates,
        )
        progress.log_plan(logger)

        for batch in batched(candidates, context.config.llm_request_max_candidates):
            payload = [
                {
                    **candidate.to_dict(),
                    "recentCatalysts": known.get(candidate.entity_id, []),
                    "marketEvidence": collector.for_upcoming(
                        candidate, today, end, economic_events=economic_events
                    ).to_dict(),
                }
                for candidate in batch
            ]
            progress.record_llm_call()
            response = context.research_llm.generate_json(
                LLMRequest(
                    prompt=template.render(
                        execution_date=today.isoformat(),
                        window_start=today.isoformat(),
                        window_end=end.isoformat(),
                        candidates=json.dumps(payload, default=str),
                    ),
                    prompt_fingerprint=template.fingerprint,
                    grounded_search=True,
                    max_tokens=4096,
                )
            )
            batch_failures = 0
            for source, row in matched_batch_rows(
                batch, response.data, label="Upcoming"
            ):
                for raw_event in row.get("events") or []:
                    try:
                        event = parse_upcoming_event(raw_event)
                    except PayloadValidationError as exc:
                        result.add_error(f"{source.symbol}: {exc}")
                        batch_failures += 1
                        continue
                    if not today <= event.event_date <= end:
                        continue
                    days = (event.event_date - today).days
                    severity, score = upcoming_catalyst_severity(
                        days, source.allocation_pct
                    )
                    event_key = f"{source.entity_id}|{event.event_type}|{event.event_date.isoformat()}"
                    item = {
                        **event.payload,
                        "user_id": source.user_id,
                        "entity_id": source.entity_id,
                        "symbol": source.symbol,
                        "relatedSymbols": source.related_symbols,
                        "name": source.name,
                        "eventKey": event_key,
                        "daysUntilEvent": days,
                        "severity": severity,
                        "severityScore": score,
                        "sources": response.sources,
                    }
                    notification_key = f"UPCOMING_CATALYST|{event_key}"
                    if writer.enqueue(
                        user_id=item["user_id"],
                        notification_key=notification_key,
                        notification_type=self.name,
                        entity_id=item["entity_id"],
                        symbol=item["symbol"],
                        event_key=event_key,
                        severity=severity,
                        title=item.get("headline") or event_key,
                        message=format_upcoming_catalyst_message(item),
                        context=item,
                    ):
                        result.notification_count += 1
                        result.accepted_count += 1
            progress.complete_batch(len(batch), failed_items=batch_failures)
            progress.log_progress(logger)

        result.diagnostics = {
            "windowStart": today.isoformat(),
            "windowEnd": end.isoformat(),
            "marketEvidence": collector.diagnostics.to_dict(),
            "workload": progress.summary(),
            **writer.to_dict(),
        }
        progress.log_completed(logger)
        return result


def run():
    from jobs.portfolio_intelligence.runtime import run_branch

    return run_branch(UpcomingCatalystBranch())
