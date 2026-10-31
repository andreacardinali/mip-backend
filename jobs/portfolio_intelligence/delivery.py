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
        count = 0
        for recipient in self._recipients:
            if recipient.entity_id != source.entity_id:
                continue
            if (
                self.branch == "DAILY_MOVER"
                and recipient.instrument_id != source.instrument_id
            ):
                continue
            if category not in recipient.enabled_categories:
                continue
            try:
                item = {
                    **payload,
                    **recipient.notification_context(),
                    **decorate(recipient),
                    "user_id": recipient.user_id,
                    "entity_id": source.entity_id,
                    "symbol": source.symbol,
                    "name": source.name,
                    "relatedSymbols": recipient.related_symbols,
                    "eventKey": event_key,
                    "notificationCategory": category,
                }
                if self.writer.enqueue(
                    user_id=recipient.user_id,
                    notification_key=f"{self.branch}|{event_key}",
                    notification_type=self.branch,
                    entity_id=source.entity_id,
                    symbol=source.symbol,
                    event_key=event_key,
                    severity=item["severity"],
                    title=item.get("headline") or event_key,
                    message=format_message(item),
                    context=item,
                ):
                    count += 1
            except Exception as exc:
                self.errors.append(
                    f"{event_key}: {recipient.user_id}/{recipient.collection_id}: {exc}"
                )
                logger.exception("Intelligence delivery failed branch=%s", self.branch)
        return count
