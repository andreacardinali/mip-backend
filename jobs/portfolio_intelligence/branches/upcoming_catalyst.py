import json
from datetime import date, timedelta

from jobs.portfolio_intelligence.branches.base import IntelligenceBranch
from jobs.portfolio_intelligence.models import BranchResult
from jobs.portfolio_intelligence.repositories.catalysts import recent_catalysts
from jobs.portfolio_intelligence.repositories.entities import load_candidates
from llm.base import LLMRequest


def _batches(items, size):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def _severity(days, weight):
    score = (3 if days <= 1 else 2 if days <= 3 else 1) + (
        3 if weight >= 20 else 2 if weight >= 5 else 1
    )
    value = (
        "CRITICAL" if score >= 6
        else "HIGH" if score >= 5
        else "MEDIUM" if score >= 4
        else "LOW"
    )
    return value, score


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

        try:
            for batch in _batches(candidates, context.config.batch_size):
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
                        except Exception:
                            continue
                        if not today <= event_date <= end:
                            continue
                        if not 0 <= int(event.get("eventConfidence", 0)) <= 100:
                            continue
                        days = (event_date - today).days
                        severity, score = _severity(days, source.allocation_pct)
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

            accepted = list({(item["user_id"], item["eventKey"]): item for item in accepted}.values())
            result.accepted_count = len(accepted)
            for item in accepted:
                notification_key = f"UPCOMING_CATALYST|{item['eventKey']}"
                message = (
                    f"📅 UPCOMING CATALYST\n{item['symbol']} | "
                    f"{item.get('eventType')} | {item.get('eventDate')}\n"
                    f"Severity: {item['severity']}\n"
                    f"{item.get('headline', '')}\n"
                    f"{item.get('eventSummary', '')}"
                )
                if context.notification_queue.enqueue(
                    user_id=item["user_id"], notification_key=notification_key,
                    notification_type=self.name, entity_id=item["entity_id"],
                    symbol=item["symbol"], event_key=item["eventKey"],
                    severity=item["severity"], title=item.get("headline") or item["eventKey"],
                    message=message, context=item,
                ):
                    result.notification_count += 1
            context.db.commit()
            result.diagnostics = {"windowStart": today.isoformat(), "windowEnd": end.isoformat()}
        except Exception as exc:
            context.db.rollback(); result.status = "FAILED"; result.errors.append(str(exc))
        return result


def run():
    from jobs.portfolio_intelligence.runtime import run_branch
    return run_branch(UpcomingCatalystBranch())
