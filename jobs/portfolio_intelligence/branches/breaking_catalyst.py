import json
import logging
from datetime import UTC, datetime

from job_runtime.collections import batched
from job_runtime.progress import BatchProgress
from jobs.portfolio_intelligence.batching import matched_batch_rows
from jobs.portfolio_intelligence.delivery import CollectionEventPublisher
from jobs.portfolio_intelligence.evidence import MarketEvidenceCollector
from jobs.portfolio_intelligence.messages import format_breaking_catalyst_message
from jobs.portfolio_intelligence.models import BranchResult
from jobs.portfolio_intelligence.parsing import (
    PayloadValidationError,
    parse_breaking_event,
)
from jobs.portfolio_intelligence.repositories.entities import (
    load_acquisition_candidates,
)
from jobs.portfolio_intelligence.scoring import breaking_catalyst_severity
from llm.base import LLMRequest

logger = logging.getLogger(__name__)


class BreakingCatalystBranch:
    name = "BREAKING_CATALYST"

    def execute(self, context):
        result = BranchResult(branch=self.name)
        candidates = load_acquisition_candidates(context.db, self.name)
        result.candidate_count = len(candidates)
        template = context.prompts.get("breaking")
        collector = MarketEvidenceCollector(context.market_data, context.config)
        economic_events = collector.common_economic_events()
        publisher = CollectionEventPublisher(context, self.name)
        progress = BatchProgress(
            job="portfolio_intelligence_breaking_catalyst",
            total_items=len(candidates),
            max_items_per_batch=context.config.llm_request_max_candidates,
        )
        progress.log_plan(logger)

        for batch in batched(candidates, context.config.llm_request_max_candidates):
            payload = [
                {
                    **item.research_context(),
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
                    category = "NEWS" if event.event_type == "NEWS" else self.name
                    event_key = f"{source.entity_id}|{event.event_type}|{event.event_day}|{event.fingerprint}"
                    item = {
                        **event.payload,
                        "notificationCategory": category,
                        "entity_id": source.entity_id,
                        "symbol": source.symbol,
                        "name": source.name,
                        "eventKey": event_key,
                        "sources": response.sources,
                        "relatedSymbols": source.related_symbols,
                    }
                    try:
                        result.notification_count += publisher.publish(
                            source,
                            event_key=event_key,
                            category=category,
                            payload=item,
                            evidence={
                                "marketEvidence": payload[batch.index(source)][
                                    "marketEvidence"
                                ],
                                "sources": response.sources,
                            },
                            decorate=lambda recipient, confidence=event.confidence: {
                                "severity": breaking_catalyst_severity(
                                    confidence, recipient.allocation_pct
                                ),
                            },
                            format_message=format_breaking_catalyst_message,
                            deliver=event.confidence >= 60,
                        )
                    except Exception as exc:
                        result.add_error(f"{source.symbol}: {exc}")
                        batch_failures += 1
                        logger.exception(
                            "Breaking event persistence failed symbol=%s", source.symbol
                        )
            progress.complete_batch(len(batch), failed_items=batch_failures)
            progress.log_progress(logger)

        result.accepted_count = publisher.accepted_count
        for error in publisher.errors:
            result.add_error(error)
        result.diagnostics = {
            "lookbackHours": context.config.breaking_lookback_hours,
            "marketEvidence": collector.diagnostics.to_dict(),
            "workload": progress.summary(),
            **publisher.writer.to_dict(),
        }
        progress.log_completed(logger)
        return result


def run():
    from jobs.portfolio_intelligence.runtime import run_branch

    return run_branch(BreakingCatalystBranch())
