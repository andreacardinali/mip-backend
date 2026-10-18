import json
from datetime import datetime, timezone
from pathlib import Path

from jobs.portfolio_intelligence.branches.base import IntelligenceBranch
from jobs.portfolio_intelligence.llm.base import LLMRequest
from jobs.portfolio_intelligence.models import BranchResult
from jobs.portfolio_intelligence.repositories.entities import load_candidates


def _batches(items, size):
    for index in range(0, len(items), size):
        yield items[index:index + size]


class BreakingCatalystBranch(IntelligenceBranch):
    name = "BREAKING_CATALYST"

    def execute(self, context):
        result = BranchResult(branch=self.name)
        candidates = load_candidates(context.db)
        result.candidate_count = len(candidates)
        template = (
            Path(__file__).parent.parent / "prompts/breaking.txt"
        ).read_text()
        accepted = []

        try:
            for batch in _batches(candidates, context.config.batch_size):
                response = context.research_llm.generate_json(
                    LLMRequest(
                        prompt=template.format(
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
                    for event in row.get("events") or []:
                        confidence = int(event.get("eventConfidence", 0))
                        if confidence < 60:
                            continue
                        fingerprint = str(
                            event.get("eventFingerprint") or ""
                        ).strip()
                        day = str(event.get("eventDate") or "")[:10]
                        if not fingerprint or not day:
                            continue
                        event_key = (
                            f"{source.symbol}|{event.get('eventType')}|"
                            f"{day}|{fingerprint}"
                        )
                        severity = (
                            "CRITICAL"
                            if confidence >= 90 and source.allocation_pct >= 20
                            else "HIGH" if confidence >= 80
                            else "MEDIUM"
                        )
                        accepted.append(
                            {
                                **event,
                                "user_id": source.user_id,
                                "entity_id": source.entity_id,
                                "symbol": source.symbol,
                                "name": source.name,
                                "eventKey": event_key,
                                "severity": severity,
                                "sources": response.sources,
                            }
                        )

            accepted = list(
                {
                    (item["user_id"], item["eventKey"]): item
                    for item in accepted
                }.values()
            )
            result.accepted_count = len(accepted)

            for item in accepted:
                notification_key = f"BREAKING_CATALYST|{item['eventKey']}"
                message = (
                    f"⚡ BREAKING CATALYST\n{item['symbol']} | "
                    f"{item.get('eventType')}\n"
                    f"Severity: {item['severity']}\n"
                    f"{item.get('headline', '')}\n"
                    f"{item.get('eventSummary', '')}\n"
                    f"{item.get('materialityReason', '')}"
                )
                if context.notification_queue.enqueue(
                    user_id=item["user_id"],
                    notification_key=notification_key,
                    notification_type=self.name,
                    entity_id=item["entity_id"],
                    symbol=item["symbol"],
                    event_key=item["eventKey"],
                    severity=item["severity"],
                    title=item.get("headline") or item["eventKey"],
                    message=message,
                    context=item,
                ):
                    result.notification_count += 1

            context.db.commit()
            result.diagnostics = {
                "lookbackHours": context.config.breaking_lookback_hours
            }
        except Exception as exc:
            context.db.rollback()
            result.status = "FAILED"
            result.errors.append(str(exc))
        return result


def run():
    from jobs.portfolio_intelligence.runtime import run_branch
    return run_branch(BreakingCatalystBranch())
