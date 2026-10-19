import json
from datetime import datetime, timezone

from jobs.portfolio_intelligence.branches.base import IntelligenceBranch
from jobs.portfolio_intelligence.messages import format_breaking_catalyst_message
from jobs.portfolio_intelligence.models import BranchResult
from jobs.portfolio_intelligence.notifications import NotificationDiagnostics
from jobs.portfolio_intelligence.parsing import (
    PayloadValidationError,
    parse_breaking_event,
)
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
                        candidates=json.dumps(
                            [item.to_dict() for item in batch],
                            default=str,
                        ),
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
                for raw_event in row.get("events") or []:
                    try:
                        event = parse_breaking_event(raw_event)
                    except PayloadValidationError as exc:
                        result.add_error(f"{source.symbol}: {exc}")
                        continue
                    if event.confidence < 60:
                        continue
                    event_key = (
                        f"{source.symbol}|{event.event_type}|"
                        f"{event.event_day}|{event.fingerprint}"
                    )
                    accepted.append(
                        {
                            **event.payload,
                            "user_id": source.user_id,
                            "entity_id": source.entity_id,
                            "symbol": source.symbol,
                            "name": source.name,
                            "eventKey": event_key,
                            "severity": breaking_catalyst_severity(
                                event.confidence,
                                source.allocation_pct,
                            ),
                            "sources": response.sources,
                        }
                    )

        accepted = deduplicate_by(
            accepted,
            key=lambda item: (item["user_id"], item["eventKey"]),
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
            **diagnostics.to_dict(),
        }
        return result


def run():
    from jobs.portfolio_intelligence.runtime import run_branch

    return run_branch(BreakingCatalystBranch())
