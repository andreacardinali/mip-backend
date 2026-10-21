import json
from datetime import date, timedelta

from job_runtime.collections import batched, deduplicate_by
from jobs.portfolio_intelligence.batching import matched_batch_rows
from jobs.portfolio_intelligence.evidence import MarketEvidenceCollector
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
from notifications.diagnostics import NotificationDiagnostics


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
        accepted = []
        collector = MarketEvidenceCollector(context.market_data, context.config)
        economic_events = collector.common_economic_events()

        for batch in batched(candidates, context.config.batch_size):
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
            response = context.research_llm.generate_json(
                LLMRequest(
                    prompt=template.render(
                        execution_date=today.isoformat(),
                        window_start=today.isoformat(),
                        window_end=end.isoformat(),
                        candidates=json.dumps(payload, default=str),
                    ),
                    grounded_search=True,
                    max_tokens=4096,
                )
            )
            for source, row in matched_batch_rows(
                batch, response.data, label="Upcoming"
            ):
                for raw_event in row.get("events") or []:
                    try:
                        event = parse_upcoming_event(raw_event)
                    except PayloadValidationError as exc:
                        result.add_error(f"{source.symbol}: {exc}")
                        continue
                    if not today <= event.event_date <= end:
                        continue
                    days = (event.event_date - today).days
                    severity, score = upcoming_catalyst_severity(
                        days, source.allocation_pct
                    )
                    event_key = f"{source.symbol}|{event.event_type}|{event.event_date.isoformat()}"
                    accepted.append(
                        {
                            **event.payload,
                            "user_id": source.user_id,
                            "entity_id": source.entity_id,
                            "symbol": source.symbol,
                            "name": source.name,
                            "eventKey": event_key,
                            "daysUntilEvent": days,
                            "severity": severity,
                            "severityScore": score,
                            "sources": response.sources,
                        }
                    )

        accepted = deduplicate_by(
            accepted, key=lambda item: (item["user_id"], item["eventKey"])
        )
        result.accepted_count = len(accepted)
        diagnostics = NotificationDiagnostics()
        for item in accepted:
            notification_key = f"UPCOMING_CATALYST|{item['eventKey']}"
            enqueue_result = context.notification_queue.enqueue(
                user_id=item["user_id"],
                notification_key=notification_key,
                notification_type=self.name,
                entity_id=item["entity_id"],
                symbol=item["symbol"],
                event_key=item["eventKey"],
                severity=item["severity"],
                title=item.get("headline") or item["eventKey"],
                message=format_upcoming_catalyst_message(item),
                context=item,
            )
            if diagnostics.record(enqueue_result):
                result.notification_count += 1
        result.diagnostics = {
            "windowStart": today.isoformat(),
            "windowEnd": end.isoformat(),
            "marketEvidence": collector.diagnostics.to_dict(),
            **diagnostics.to_dict(),
        }
        return result


def run():
    from jobs.portfolio_intelligence.runtime import run_branch

    return run_branch(UpcomingCatalystBranch())
