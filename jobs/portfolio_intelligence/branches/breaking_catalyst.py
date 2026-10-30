import json
import logging
from datetime import UTC, datetime

from job_runtime.collections import batched
from job_runtime.progress import BatchProgress
from jobs.portfolio_intelligence.batching import matched_batch_rows
from jobs.portfolio_intelligence.evidence import MarketEvidenceCollector
from jobs.portfolio_intelligence.incremental import IncrementalNotificationWriter
from jobs.portfolio_intelligence.messages import format_breaking_catalyst_message
from jobs.portfolio_intelligence.models import BranchResult
from jobs.portfolio_intelligence.parsing import (
    PayloadValidationError,
    parse_breaking_event,
)
from jobs.portfolio_intelligence.repositories.entities import load_candidates
from jobs.portfolio_intelligence.scoring import breaking_catalyst_severity
from llm.base import LLMRequest

logger = logging.getLogger(__name__)


class BreakingCatalystBranch:
    name = "BREAKING_CATALYST"

    def execute(self, context):
        result = BranchResult(branch=self.name)
        candidates = load_candidates(context.db)
        result.candidate_count = len(candidates)
        template = context.prompts.get("breaking")
        collector = MarketEvidenceCollector(context.market_data, context.config)
        economic_events = collector.common_economic_events()
        writer = IncrementalNotificationWriter(
            context.notification_queue, db=context.db
        )
        progress = BatchProgress(
            job="portfolio_intelligence_breaking_catalyst",
            total_items=len(candidates),
            max_items_per_batch=context.config.llm_request_max_candidates,
        )
        progress.log_plan(logger)

        for batch in batched(candidates, context.config.llm_request_max_candidates):
            payload = [
                {
                    **item.to_dict(),
                    "marketEvidence": collector.for_breaking(
                        item, economic_events=economic_events
                    ).to_dict(),
                }
                for item in batch
            ]
            progress.record_llm_call()
            response = context.research_llm.generate_json(
                LLMRequest(
                    prompt=template.render(
                        lookback_hours=context.config.breaking_lookback_hours,
                        execution_date=datetime.now(UTC).isoformat(),
                        candidates=json.dumps(payload, default=str),
                    ),
                    prompt_fingerprint=template.fingerprint,
                    grounded_search=True,
                    max_tokens=8192,
                )
            )
            batch_failures = 0
            for source, row in matched_batch_rows(
                batch, response.data, label="Breaking"
            ):
                for raw_event in row.get("events") or []:
                    try:
                        event = parse_breaking_event(raw_event)
                    except PayloadValidationError as exc:
                        result.add_error(f"{source.symbol}: {exc}")
                        batch_failures += 1
                        continue
                    if event.confidence < 60:
                        continue
                    event_key = f"{source.entity_id}|{event.event_type}|{event.event_day}|{event.fingerprint}"
                    item = {
                        **event.payload,
                        "user_id": source.user_id,
                        "entity_id": source.entity_id,
                        "symbol": source.symbol,
                        "name": source.name,
                        "eventKey": event_key,
                        "severity": breaking_catalyst_severity(
                            event.confidence, source.allocation_pct
                        ),
                        "sources": response.sources,
                        "relatedSymbols": source.related_symbols,
                    }
                    notification_key = f"BREAKING_CATALYST|{event_key}"
                    if writer.enqueue(
                        user_id=item["user_id"],
                        notification_key=notification_key,
                        notification_type=self.name,
                        entity_id=item["entity_id"],
                        symbol=item["symbol"],
                        event_key=event_key,
                        severity=item["severity"],
                        title=item.get("headline") or event_key,
                        message=format_breaking_catalyst_message(item),
                        context=item,
                    ):
                        result.notification_count += 1
                        result.accepted_count += 1
            progress.complete_batch(len(batch), failed_items=batch_failures)
            progress.log_progress(logger)

        result.diagnostics = {
            "lookbackHours": context.config.breaking_lookback_hours,
            "marketEvidence": collector.diagnostics.to_dict(),
            "workload": progress.summary(),
            **writer.to_dict(),
        }
        progress.log_completed(logger)
        return result


def run():
    from jobs.portfolio_intelligence.runtime import run_branch

    return run_branch(BreakingCatalystBranch())
