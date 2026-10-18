"""Compatibility wrapper backed by the reusable Telegram sender."""

from notifications.telegram import TelegramSender


class TelegramNotifier(TelegramSender):
    def __init__(
        self,
        token: str | None,
        chat_id: str | None,
        timeout: float = 30.0,
    ) -> None:
        sender = TelegramSender.for_user_source(
            bot_token=token or "",
            chat_id=chat_id or "",
            timeout=timeout,
        )
        super().__init__(sender.config)

    @property
    def token(self) -> str:
        return self.config.bot_token

    @property
    def chat_id(self) -> str:
        return self.config.chat_id

    @property
    def timeout(self) -> float:
        return self.config.timeout
