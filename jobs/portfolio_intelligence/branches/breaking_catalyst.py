import json
from datetime import UTC, datetime

from job_runtime.collections import batched, deduplicate_by
from jobs.portfolio_intelligence.batching import matched_batch_rows
from jobs.portfolio_intelligence.evidence import MarketEvidenceCollector
from jobs.portfolio_intelligence.messages import format_breaking_catalyst_message
from jobs.portfolio_intelligence.models import BranchResult
from jobs.portfolio_intelligence.parsing import (
    PayloadValidationError,
    parse_breaking_event,
)
from jobs.portfolio_intelligence.repositories.entities import load_candidates
from jobs.portfolio_intelligence.scoring import breaking_catalyst_severity
from llm.base import LLMRequest
from notifications.diagnostics import NotificationDiagnostics


class BreakingCatalystBranch:
    name = "BREAKING_CATALYST"

    def execute(self, context):
        result = BranchResult(branch=self.name)
        candidates = load_candidates(context.db)
        result.candidate_count = len(candidates)
        template = context.prompts.get("breaking")
        accepted = []
        collector = MarketEvidenceCollector(context.market_data, context.config)
        economic_events = collector.common_economic_events()
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
            response = context.research_llm.generate_json(
                LLMRequest(
                    prompt=template.render(
                        lookback_hours=context.config.breaking_lookback_hours,
                        execution_date=datetime.now(UTC).isoformat(),
                        candidates=json.dumps(payload, default=str),
                    ),
                    grounded_search=True,
                    max_tokens=8192,
                )
            )
            for source, row in matched_batch_rows(
                batch, response.data, label="Breaking"
            ):
                for raw_event in row.get("events") or []:
                    try:
                        event = parse_breaking_event(raw_event)
                    except PayloadValidationError as exc:
                        result.add_error(f"{source.symbol}: {exc}")
                        continue
                    if event.confidence < 60:
                        continue
                    event_key = f"{source.entity_id}|{event.event_type}|{event.event_day}|{event.fingerprint}"
                    accepted.append(
                        {
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
                    )
        accepted = deduplicate_by(
            accepted, key=lambda item: (item["user_id"], item["eventKey"])
        )
        result.accepted_count = len(accepted)
        diagnostics = NotificationDiagnostics()
        for item in accepted:
            notification_key = f"BREAKING_CATALYST|{item['eventKey']}"
            enqueue_result = context.notification_queue.enqueue(
                user_id=item["user_id"],
                notification_key=notification_key,
                notification_type=self.name,
                entity_id=item["entity_id"],
                symbol=item["symbol"],
                event_key=item["eventKey"],
                severity=item["severity"],
                title=item.get("headline") or item["eventKey"],
                message=format_breaking_catalyst_message(item),
                context=item,
            )
            if diagnostics.record(enqueue_result):
                result.notification_count += 1
        result.diagnostics = {
            "lookbackHours": context.config.breaking_lookback_hours,
            "marketEvidence": collector.diagnostics.to_dict(),
            **diagnostics.to_dict(),
        }
        return result


def run():
    from jobs.portfolio_intelligence.runtime import run_branch

    return run_branch(BreakingCatalystBranch())
