import json
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from job_runtime.progress import BatchProgress
from jobs.portfolio_intelligence.delivery import CollectionEventPublisher
from jobs.portfolio_intelligence.evidence import (
    MarketEvidenceCollector,
    instrument_reference,
)
from jobs.portfolio_intelligence.messages import format_daily_mover_message
from jobs.portfolio_intelligence.models import BranchResult
from jobs.portfolio_intelligence.parsing import parse_daily_validation
from jobs.portfolio_intelligence.repositories.entities import (
    load_acquisition_candidates,
)
from jobs.portfolio_intelligence.scoring import daily_mover_severity
from llm.base import LLMRequest
from llm.evidence import select_sources
from market_analysis.technical import analyze_price_bars
from market_data.models import HistoryRequest
from market_time.models import MarketStatus

logger = logging.getLogger(__name__)


class DailyMoverBranch:
    name = "DAILY_MOVER"

    def execute(self, context):
        result = BranchResult(branch=self.name)
        entities = load_acquisition_candidates(context.db, self.name)
        result.candidate_count = len(entities)
        research_template = context.prompts.get("daily_research")
        validation_template = context.prompts.get("daily_validate")
        collector = MarketEvidenceCollector(context.market_data, context.config)
        economic_events = collector.common_economic_events()
        publisher = CollectionEventPublisher(context, self.name)
        progress = BatchProgress(
            job="portfolio_intelligence_daily_mover",
            total_items=len(entities),
            max_items_per_batch=1,
        )
        progress.log_plan(logger)

        for entity in entities:
            failed = 0
            try:
                reference = instrument_reference(entity)
                market_clock = context.market_time.snapshot(entity.exchange)
                if market_clock.status != MarketStatus.OPEN or not market_clock.session:
                    progress.complete_batch(1)
                    progress.log_progress(logger)
                    continue

                quote = context.market_data.get_quote(reference)
                quote_at = datetime.fromtimestamp(int(quote.timestamp), UTC)
                quote_age = datetime.now(UTC) - quote_at
                if quote_age < timedelta(0) or quote_age > timedelta(minutes=10):
                    progress.complete_batch(1)
                    progress.log_progress(logger)
                    continue
                quote_clock = context.market_time.snapshot(entity.exchange, at=quote_at)
                if (
                    quote_clock.status != MarketStatus.OPEN
                    or not quote_clock.session
                    or quote_clock.session.session_date
                    != market_clock.session.session_date
                ):
                    progress.complete_batch(1)
                    progress.log_progress(logger)
                    continue

                bars = context.market_data.get_ohlcv(
                    HistoryRequest(
                        instrument=reference,
                        period=context.config.history_range,
                        interval=context.config.history_interval,
                    )
                )
                completed_bars = sorted(
                    (
                        bar
                        for bar in bars
                        if bar.timestamp
                        < int(market_clock.session.opens_at.timestamp())
                    ),
                    key=lambda bar: bar.timestamp,
                )
                if (
                    len(bars) < context.config.min_history_rows
                    or len(completed_bars) < 2
                ):
                    progress.complete_batch(1)
                    progress.log_progress(logger)
                    continue
                snapshot = analyze_price_bars(completed_bars)
                previous_close = completed_bars[-1].close if completed_bars else None
                if snapshot is not None and previous_close:
                    snapshot = replace(
                        snapshot,
                        market_price=quote.price,
                        previous_close=previous_close,
                        daily_change_pct=(quote.price - previous_close)
                        / previous_close
                        * 100,
                        observed_timestamp=quote.timestamp,
                    )
                if (
                    snapshot is None
                    or abs(snapshot.daily_change_pct)
                    <= context.config.daily_move_min_pct
                ):
                    progress.complete_batch(1)
                    progress.log_progress(logger)
                    continue
                observed = {
                    **entity.research_context(),
                    **snapshot.to_dict(),
                    "marketEvidence": collector.for_daily_mover(
                        entity, economic_events=economic_events
                    ).to_dict(),
                }
                progress.record_llm_call()
                research = context.research_llm.generate_json(
                    LLMRequest(
                        prompt=research_template.render(
                            observed=json.dumps(observed, default=str)
                        ),
                        prompt_fingerprint=research_template.fingerprint,
                        grounded_search=True,
                        max_tokens=4096,
                    )
                )
                progress.record_llm_call()
                final = context.validation_llm.generate_json(
                    LLMRequest(
                        prompt=validation_template.render(
                            observed=json.dumps(observed, default=str),
                            catalyst=json.dumps(
                                research.data.get("catalyst"), default=str
                            ),
                            supports=json.dumps(
                                research.data.get("supports") or research.sources,
                                default=str,
                            ),
                        ),
                        prompt_fingerprint=validation_template.fingerprint,
                        max_tokens=900,
                    )
                )
                validation = parse_daily_validation(
                    final.data, expected_symbol=entity.symbol
                )
                item = {
                    **validation.payload,
                    **observed,
                    "entity_id": entity.entity_id,
                    "sourcesUsed": select_sources(
                        validation.source_indices, research.sources
                    ),
                }
                observed_day = market_clock.session.session_date.isoformat()
                direction = "up" if item["dailyChangePct"] > 0 else "down"
                event_key = f"{entity.instrument_id}|{observed_day}|{direction}"
                result.notification_count += publisher.publish(
                    entity,
                    event_key=event_key,
                    category=self.name,
                    payload=item,
                    evidence={
                        "marketEvidence": observed["marketEvidence"],
                        "sourcesUsed": item["sourcesUsed"],
                    },
                    decorate=lambda recipient, change=snapshot.daily_change_pct: {
                        "severity": daily_mover_severity(
                            change, recipient.allocation_pct
                        ),
                        "portfolioImpactPct": (
                            change * recipient.allocation_pct / 100.0
                            if recipient.exposure_available
                            else None
                        ),
                    },
                    format_message=format_daily_mover_message,
                )
            except Exception as exc:
                failed = 1
                result.add_error(f"{entity.symbol}: {exc}")
                logger.exception("Daily Mover entity failed symbol=%s", entity.symbol)
            progress.complete_batch(1, failed_items=failed)
            progress.log_progress(logger)

        result.accepted_count = publisher.accepted_count
        for error in publisher.errors:
            result.add_error(error)
        result.diagnostics = {
            "entityErrors": len(result.errors),
            "marketEvidence": collector.diagnostics.to_dict(),
            "workload": progress.summary(),
            **publisher.writer.to_dict(),
        }
        progress.log_completed(logger)
        return result


def run():
    from jobs.portfolio_intelligence.runtime import run_branch

    return run_branch(DailyMoverBranch())
