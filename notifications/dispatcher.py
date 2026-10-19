import logging

from sqlalchemy.orm import Session

from notifications.config import NotificationConfig
from notifications.models import NotificationDelivery
from notifications.plugins.telegram import TelegramPlugin
from notifications.repository import NotificationRepository

logger = logging.getLogger(__name__)


class NotificationDispatcher:
    def __init__(
        self,
        db: Session,
        config: NotificationConfig,
    ) -> None:
        self.db = db
        self.config = config
        self.repo = NotificationRepository(db)
        self.plugins = {
            "TELEGRAM": TelegramPlugin(timeout_seconds=config.http_timeout_seconds),
        }

    def run_once(self) -> dict:
        if not self.config.enabled:
            return {
                "status": "DISABLED",
                "claimed": 0,
                "sent": 0,
                "retry": 0,
                "failed": 0,
            }

        released = self.repo.release_stale(self.config.processing_timeout_seconds)
        deliveries = self.repo.claim(self.config.batch_size)
        self.db.commit()

        sent = 0
        retry = 0
        failed = 0
        errors: list[dict[str, str]] = []

        for delivery in deliveries:
            try:
                plugin = self._plugin_for(delivery)
                result = plugin.send(delivery)
                self.repo.mark_sent(
                    notification_id=delivery.notification_id,
                    provider_message_id=result.provider_message_id,
                )
                self.db.commit()
                sent += 1
            except Exception as exc:
                self.db.rollback()
                status = self.repo.mark_failed(
                    notification_id=delivery.notification_id,
                    error=str(exc),
                    max_retries=self.config.max_retries,
                    retry_base_seconds=self.config.retry_base_seconds,
                )
                self.db.commit()
                if status == "RETRY":
                    retry += 1
                else:
                    failed += 1
                errors.append(
                    {
                        "notification_id": delivery.notification_id,
                        "error": str(exc),
                    }
                )
                logger.exception(
                    "Notification delivery failed notification_id=%s",
                    delivery.notification_id,
                )

        status = "SUCCESS"
        if failed or retry:
            status = "PARTIAL" if sent else "FAILED"

        return {
            "status": status,
            "released_stale": released,
            "claimed": len(deliveries),
            "sent": sent,
            "retry": retry,
            "failed": failed,
            "errors": errors,
        }

    def _plugin_for(self, delivery: NotificationDelivery):
        try:
            return self.plugins[delivery.channel.upper()]
        except KeyError as exc:
            raise ValueError(
                f"Unsupported notification channel: {delivery.channel}"
            ) from exc
