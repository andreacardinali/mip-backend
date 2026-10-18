from datetime import datetime, timezone

from jobs.portfolio_intelligence.notifications.telegram import TelegramNotifier


class NotificationService:
    def __init__(self, repo, request_timeout: float, send_enabled: bool):
        self.repo = repo
        self.request_timeout = request_timeout
        self.send_enabled = send_enabled

    def _notifier_for(self, user_id: str) -> TelegramNotifier:
        source = self.repo.get_delivery_source(user_id, "TELEGRAM")
        if not source:
            raise ValueError(
                f"Sorgente Telegram non configurata per user_id={user_id}"
            )

        config = source.get("config") or {}
        token = config.get("bot_token") or config.get("token")
        chat_id = config.get("chat_id") or source.get("external_id")

        if not token:
            raise ValueError(
                f"Token Telegram mancante in user_sources per user_id={user_id}"
            )
        if not chat_id:
            raise ValueError(
                f"Chat ID Telegram mancante in user_sources per user_id={user_id}"
            )

        return TelegramNotifier(
            token=token,
            chat_id=str(chat_id),
            timeout=self.request_timeout,
        )

    def publish(self, *, user_id, notification_id, branch, entity_id, symbol,
                event_key, severity, title, message, context):
        if self.repo.notification_exists(user_id, notification_id):
            return False

        status = "DRY_RUN"
        sent_at = None

        if self.send_enabled:
            self._notifier_for(user_id).send(message)
            status = "SENT"
            sent_at = datetime.now(timezone.utc)

        self.repo.upsert_notification(
            user_id=user_id,
            notification_key=notification_id,
            branch=branch,
            entity_id=entity_id,
            symbol=symbol,
            event_key=event_key,
            severity=severity,
            title=title,
            message=message,
            context=context,
            status=status,
            sent_at=sent_at,
        )
        return True
