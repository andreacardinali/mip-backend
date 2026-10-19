import json
from datetime import datetime, timezone

from jobs.portfolio_intelligence.branches.base import IntelligenceBranch
from jobs.portfolio_intelligence.messages import format_breaking_catalyst_message
from jobs.portfolio_intelligence.models import BranchResult
from jobs.portfolio_intelligence.repositories.entities import load_candidates
from jobs.portfolio_intelligence.scoring import breaking_catalyst_severity
from jobs.portfolio_intelligence.utils import batched, deduplicate_by
from llm.base import LLMRequest


class BreakingCatalystBranch(IntelligenceBranch):
    name = "BREAKING_CATALYST"

    def execute(self, context):
        result = BranchResult(branch=self.name)
        candidates = load_candidates(context.db)
        result.candidate_count = len(candidates)
        template = context.prompts.get("breaking")
        accepted = []

        for batch in batched(candidates, context.config.batch_size):
            response = context.research_llm.generate_json(
                LLMRequest(
                    prompt=template.render(
                        lookback_hours=context.config.breaking_lookback_hours,
                        execution_date=datetime.now(timezone.utc).isoformat(),
                        candidates=json.dumps([item.to_dict() for item in batch], default=str),
                    ),
                    grounded_search=True,
                    max_tokens=8192,
                )
            )
            rows = response.data.get("results") or []
            if len(rows) != len(batch):
                raise ValueError("Breaking LLM result count mismatch")
            for source, row in zip(batch, rows):
                if row.get("symbol") != source.symbol:
                    raise ValueError("Breaking LLM symbol mismatch")
                for event in row.get("events") or []:
                    try:
                        confidence = int(event.get("eventConfidence", 0))
                    except (TypeError, ValueError):
                        result.add_error(f"{source.symbol}: invalid breaking confidence")
                        continue
                    if confidence < 60:
                        continue
                    fingerprint = str(event.get("eventFingerprint") or "").strip()
                    day = str(event.get("eventDate") or "")[:10]
                    if not fingerprint or not day:
                        result.add_error(f"{source.symbol}: incomplete breaking event identity")
                        continue
                    event_key = f"{source.symbol}|{event.get('eventType')}|{day}|{fingerprint}"
                    accepted.append({
                        **event,
                        "user_id": source.user_id,
                        "entity_id": source.entity_id,
                        "symbol": source.symbol,
                        "name": source.name,
                        "eventKey": event_key,
                        "severity": breaking_catalyst_severity(confidence, source.allocation_pct),
                        "sources": response.sources,
                    })

        accepted = deduplicate_by(
            accepted,
            key=lambda item: (item["user_id"], item["eventKey"]),
        )
        result.accepted_count = len(accepted)
        for item in accepted:
            notification_key = f"BREAKING_CATALYST|{item['eventKey']}"
            if context.notification_queue.enqueue(
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
            ):
                result.notification_count += 1
        result.diagnostics = {
            "lookbackHours": context.config.breaking_lookback_hours,
        }
        return result


def run():
    from jobs.portfolio_intelligence.runtime import run_branch
    return run_branch(BreakingCatalystBranch())
