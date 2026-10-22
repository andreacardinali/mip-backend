import json
import logging
import time
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
from llm.rate_limit import estimate_tokens
from notifications.diagnostics import NotificationDiagnostics

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
        accepted = []
        collector = MarketEvidenceCollector(context.market_data, context.config)
        economic_events = collector.common_economic_events()

        for batch_index, batch in enumerate(
            batched(
                candidates,
                context.config.batch_size,
            ),
            start=1,
        ):
            batch_symbols = [candidate.symbol for candidate in batch]

            logger.info(
                "Upcoming Catalyst batch started " "batch=%s symbols=%s",
                batch_index,
                batch_symbols,
            )

            payload = [
                {
                    **candidate.to_dict(),
                    "recentCatalysts": known.get(
                        candidate.entity_id,
                        [],
                    ),
                    "marketEvidence": (
                        collector.for_upcoming(
                            candidate,
                            today,
                            end,
                            economic_events=(economic_events),
                        ).to_dict()
                    ),
                }
                for candidate in batch
            ]

            for candidate_payload in payload:
                evidence = candidate_payload.get("marketEvidence") or {}

                logger.debug(
                    "Upcoming Catalyst candidate "
                    "symbol=%s "
                    "recent_catalysts=%s "
                    "earnings=%s "
                    "economic_events=%s "
                    "etf_holdings=%s "
                    "etf_countries=%s "
                    "etf_sectors=%s",
                    candidate_payload.get("symbol"),
                    len(candidate_payload.get("recentCatalysts") or []),
                    len(evidence.get("earnings") or []),
                    len(evidence.get("economicEvents") or []),
                    len(evidence.get("etfHoldings") or []),
                    len(evidence.get("etfCountries") or []),
                    len(evidence.get("etfSectors") or []),
                )

            prompt = template.render(
                execution_date=today.isoformat(),
                window_start=today.isoformat(),
                window_end=end.isoformat(),
                candidates=json.dumps(
                    payload,
                    default=str,
                ),
            )

            estimated_tokens = estimate_tokens(
                prompt,
                4096,
            )

            logger.info(
                "Upcoming Catalyst LLM request "
                "batch=%s "
                "symbols=%s "
                "prompt_characters=%s "
                "estimated_tokens=%s "
                "max_tokens=%s",
                batch_index,
                batch_symbols,
                len(prompt),
                estimated_tokens,
                4096,
            )

            llm_started = time.perf_counter()

            try:
                response = context.research_llm.generate_json(
                    LLMRequest(
                        prompt=prompt,
                        grounded_search=True,
                        max_tokens=4096,
                    )
                )
            except Exception:
                logger.exception(
                    "Upcoming Catalyst LLM request "
                    "failed "
                    "batch=%s symbols=%s "
                    "elapsed_seconds=%.3f",
                    batch_index,
                    batch_symbols,
                    (time.perf_counter() - llm_started),
                )
                raise

            logger.info(
                "Upcoming Catalyst LLM request "
                "completed "
                "batch=%s symbols=%s "
                "elapsed_seconds=%.3f "
                "result_count=%s",
                batch_index,
                batch_symbols,
                (time.perf_counter() - llm_started),
                len(response.data.get("results") or []),
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
