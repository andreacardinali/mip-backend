import logging
from collections.abc import Callable
from typing import Any

from jobs.portfolio_intelligence.incremental import IncrementalNotificationWriter
from jobs.portfolio_intelligence.models import EntityCandidate
from jobs.portfolio_intelligence.repositories.catalysts import save_intelligence_event
from jobs.portfolio_intelligence.repositories.entities import load_candidates

logger = logging.getLogger(__name__)


class CollectionEventPublisher:
    def __init__(self, context, branch: str):
        self.context = context
        self.branch = branch
        self.writer = IncrementalNotificationWriter(
            context.notification_queue, db=context.db
        )
        self._recipients: list[EntityCandidate] | None = None
        self._saved_keys: set[str] = set()
        self.errors: list[str] = []

    @property
    def accepted_count(self) -> int:
        return len(self._saved_keys)

    def publish(
        self,
        source: EntityCandidate,
        *,
        event_key: str,
        category: str,
        payload: dict[str, Any],
        evidence: dict[str, Any],
        decorate: Callable[[EntityCandidate], dict[str, Any]],
        format_message: Callable[[dict[str, Any]], str],
        deliver: bool = True,
    ) -> int:
        if event_key not in self._saved_keys:
            save_intelligence_event(
                self.context.db,
                branch=self.branch,
                event_key=event_key,
                entity_id=source.entity_id,
                instrument_id=source.instrument_id,
                category=category,
                payload=payload,
                evidence=evidence,
            )
            self._saved_keys.add(event_key)
        if not deliver:
            return 0
        if self._recipients is None:
            try:
                self._recipients = load_candidates(self.context.db, self.branch)
            except Exception as exc:
                self.context.db.rollback()
                self.errors.append(f"{event_key}: recipient lookup failed: {exc}")
                logger.exception(
                    "Intelligence recipient lookup failed branch=%s", self.branch
                )
                return 0
        matching_recipients = [
            recipient
            for recipient in self._recipients
            if recipient.entity_id == source.entity_id
            and (
                self.branch != "DAILY_MOVER"
                or recipient.instrument_id == source.instrument_id
            )
            and category in recipient.enabled_categories
        ]
        recipients_by_user: dict[str, list[EntityCandidate]] = {}
        for recipient in matching_recipients:
            recipients_by_user.setdefault(recipient.user_id, []).append(recipient)

        count = 0
        for user_id, user_recipients in recipients_by_user.items():
            portfolios = [
                recipient
                for recipient in user_recipients
                if (recipient.collection_type or "").upper() == "PORTFOLIO"
            ]
            recipient = max(
                portfolios or user_recipients,
                key=lambda candidate: (
                    candidate.exposure_available,
                    candidate.allocation_pct,
                ),
            )
            portfolio_exposure = []
            for portfolio in portfolios:
                exposure = {
                    "collectionName": portfolio.collection_name,
                    "allocationPct": (
                        portfolio.allocation_pct
                        if portfolio.exposure_available
                        else None
                    ),
                    "positionMarketValue": (
                        portfolio.market_value if portfolio.exposure_available else None
                    ),
                }
                exposure.update(decorate(portfolio))
                portfolio_exposure.append(exposure)
            collection_memberships = [
                {
                    "collectionType": member.collection_type,
                    "collectionId": member.collection_id,
                    "collectionName": member.collection_name,
                }
                for member in user_recipients
            ]
            decorations = [decorate(portfolio) for portfolio in portfolios]
            if not decorations:
                decorations = [decorate(recipient)]
            severity_rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
            decoration = max(
                decorations,
                key=lambda value: severity_rank.get(value.get("severity"), -1),
            )
            if recipient.entity_id != source.entity_id:
                continue
            try:
                item = {
                    **payload,
                    **recipient.notification_context(),
                    **decoration,
                    "user_id": user_id,
                    "entity_id": source.entity_id,
                    "symbol": source.symbol,
                    "name": source.name,
                    "relatedSymbols": recipient.related_symbols,
                    "collections": collection_memberships,
                    "portfolioExposure": portfolio_exposure,
                    "inPortfolio": bool(portfolios),
                    "inWatchlist": any(
                        (member.collection_type or "").upper() == "WATCHLIST"
                        for member in user_recipients
                    ),
                    "eventKey": event_key,
                    "notificationCategory": category,
                }
                if self.writer.enqueue(
                    user_id=user_id,
                    notification_key=f"{self.branch}|{event_key}",
                    notification_type=self.branch,
                    entity_id=source.entity_id,
                    symbol=source.symbol,
                    event_key=event_key,
                    severity=item["severity"],
                    title=item.get("headline") or event_key,
                    message=format_message(item),
                    context=item,
                    source_id=recipient.notification_source_id,
                ):
                    count += 1
            except Exception as exc:
                self.errors.append(
                    f"{event_key}: {user_id}/{recipient.collection_id}: {exc}"
                )
                logger.exception("Intelligence delivery failed branch=%s", self.branch)
        return count
