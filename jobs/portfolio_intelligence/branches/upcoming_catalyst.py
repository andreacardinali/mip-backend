import json
from datetime import date, timedelta

from jobs.portfolio_intelligence.branches.base import IntelligenceBranch
from jobs.portfolio_intelligence.messages import format_upcoming_catalyst_message
from jobs.portfolio_intelligence.models import BranchResult
from jobs.portfolio_intelligence.repositories.catalysts import recent_catalysts
from jobs.portfolio_intelligence.repositories.entities import load_candidates
from jobs.portfolio_intelligence.scoring import upcoming_catalyst_severity
from jobs.portfolio_intelligence.utils import batched, deduplicate_by
from llm.base import LLMRequest


class UpcomingCatalystBranch(IntelligenceBranch):
    name = "UPCOMING_CATALYST"

    def execute(self, context):
        result = BranchResult(branch=self.name)
        candidates = load_candidates(context.db)
        result.candidate_count = len(candidates)
        today = date.today()
        end = today + timedelta(days=context.config.upcoming_window_days)
        known = recent_catalysts(
            context.db,
            [candidate.entity_id for candidate in candidates],
            30,
        )
        template = context.prompts.get("upcoming")
        accepted = []

        for batch in batched(candidates, context.config.batch_size):
            payload = [
                {
                    **candidate.to_dict(),
                    "recentCatalysts": known.get(candidate.entity_id, []),
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
                    max_tokens=8192,
                )
            )
            rows = response.data.get("results") or []
            if len(rows) != len(batch):
                raise ValueError("Upcoming LLM result count mismatch")
            for source, row in zip(batch, rows):
                if row.get("symbol") != source.symbol:
                    raise ValueError("Upcoming LLM symbol mismatch")
                for event in row.get("events") or []:
                    try:
                        event_date = date.fromisoformat(event["eventDate"][:10])
                        confidence = int(event.get("eventConfidence", 0))
                    except (KeyError, TypeError, ValueError):
                        result.add_error(f"{source.symbol}: invalid upcoming event payload")
                        continue
                    if not today <= event_date <= end or not 0 <= confidence <= 100:
                        continue
                    days = (event_date - today).days
                    severity, score = upcoming_catalyst_severity(days, source.allocation_pct)
                    event_key = f"{source.symbol}|{event.get('eventType')}|{event_date.isoformat()}"
                    accepted.append({
                        **event,
                        "user_id": source.user_id,
                        "entity_id": source.entity_id,
                        "symbol": source.symbol,
                        "name": source.name,
                        "eventKey": event_key,
                        "daysUntilEvent": days,
                        "severity": severity,
                        "severityScore": score,
                        "sources": response.sources,
                    })

        accepted = deduplicate_by(
            accepted,
            key=lambda item: (item["user_id"], item["eventKey"]),
        )
        result.accepted_count = len(accepted)
        for item in accepted:
            notification_key = f"UPCOMING_CATALYST|{item['eventKey']}"
            if context.notification_queue.enqueue(
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
            ):
                result.notification_count += 1
        result.diagnostics = {
            "windowStart": today.isoformat(),
            "windowEnd": end.isoformat(),
        }
        return result


def run():
    from jobs.portfolio_intelligence.runtime import run_branch
    return run_branch(UpcomingCatalystBranch())
