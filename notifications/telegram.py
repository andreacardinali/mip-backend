from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from config.settings import settings


TELEGRAM_API_BASE_URL = "https://api.telegram.org"
TELEGRAM_TEXT_LIMIT = 4096
DEFAULT_CHUNK_SIZE = 4000


class TelegramDeliveryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    bot_token: str
    chat_id: str
    timeout: float = 30.0
    parse_mode: str | None = None
    disable_web_page_preview: bool = True
    disable_notification: bool = False
    chunk_size: int = DEFAULT_CHUNK_SIZE

    @classmethod
    def for_platform_admin(cls) -> "TelegramConfig":
        """Load platform-administrator credentials from the master .env."""
        return cls(
            bot_token=_required_setting("TELEGRAM_BOT_TOKEN"),
            chat_id=_required_setting("TELEGRAM_CHAT_ID"),
            timeout=float(settings.get("NOTIFICATION_HTTP_TIMEOUT", 30.0)),
            parse_mode=(
                str(settings.get("TELEGRAM_PARSE_MODE", "")).strip()
                or None
            ),
            disable_web_page_preview=_as_bool(
                settings.get("TELEGRAM_DISABLE_WEB_PAGE_PREVIEW", "true")
            ),
            disable_notification=_as_bool(
                settings.get("TELEGRAM_DISABLE_NOTIFICATION", "false")
            ),
            chunk_size=_validated_chunk_size(
                settings.get("TELEGRAM_MESSAGE_CHUNK_SIZE", DEFAULT_CHUNK_SIZE)
            ),
        )

    @classmethod
    def for_user_source(
        cls,
        *,
        bot_token: str,
        chat_id: str,
        timeout: float = 30.0,
        parse_mode: str | None = None,
        disable_web_page_preview: bool = True,
        disable_notification: bool = False,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> "TelegramConfig":
        """Build credentials originating exclusively from user_sources."""
        clean_token = str(bot_token or "").strip()
        clean_chat_id = str(chat_id or "").strip()

        if not clean_token:
            raise ValueError("user_sources: Telegram bot_token is missing")
        if not clean_chat_id:
            raise ValueError("user_sources: Telegram chat_id is missing")

        return cls(
            bot_token=clean_token,
            chat_id=clean_chat_id,
            timeout=timeout,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
            disable_notification=disable_notification,
            chunk_size=_validated_chunk_size(chunk_size),
        )


@dataclass(frozen=True, slots=True)
class TelegramMessageResult:
    message_id: int
    chat_id: int | str
    raw: dict[str, Any]


class TelegramSender:
    def __init__(
        self,
        config: TelegramConfig,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self._client = client

    @classmethod
    def for_platform_admin(cls) -> "TelegramSender":
        return cls(TelegramConfig.for_platform_admin())

    @classmethod
    def for_user_source(
        cls,
        *,
        bot_token: str,
        chat_id: str,
        timeout: float = 30.0,
        parse_mode: str | None = None,
        disable_web_page_preview: bool = True,
        disable_notification: bool = False,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> "TelegramSender":
        return cls(
            TelegramConfig.for_user_source(
                bot_token=bot_token,
                chat_id=chat_id,
                timeout=timeout,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
                disable_notification=disable_notification,
                chunk_size=chunk_size,
            )
        )

    def send(
        self,
        message: str,
        *,
        chat_id: str | int | None = None,
        parse_mode: str | None = None,
        disable_notification: bool | None = None,
    ) -> list[TelegramMessageResult]:
        text = message.strip()
        if not text:
            raise ValueError("Il messaggio Telegram non può essere vuoto")

        destination = chat_id if chat_id is not None else self.config.chat_id
        chunks = _split_message(text, self.config.chunk_size)
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=self.config.timeout)

        try:
            return [
                self._send_chunk(
                    client,
                    chunk,
                    chat_id=destination,
                    parse_mode=parse_mode,
                    disable_notification=disable_notification,
                )
                for chunk in chunks
            ]
        finally:
            if owns_client:
                client.close()

    def _send_chunk(
        self,
        client: httpx.Client,
        text: str,
        *,
        chat_id: str | int,
        parse_mode: str | None,
        disable_notification: bool | None,
    ) -> TelegramMessageResult:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": self.config.disable_web_page_preview,
            "disable_notification": (
                self.config.disable_notification
                if disable_notification is None
                else disable_notification
            ),
        }

        effective_parse_mode = (
            self.config.parse_mode if parse_mode is None else parse_mode
        )
        if effective_parse_mode:
            payload["parse_mode"] = effective_parse_mode

        response = client.post(self._endpoint("sendMessage"), json=payload)
        try:
            body = response.json()
        except ValueError as exc:
            response.raise_for_status()
            raise TelegramDeliveryError(
                "Telegram ha restituito una risposta non JSON"
            ) from exc

        if response.is_error or not body.get("ok"):
            description = body.get("description", "errore Telegram sconosciuto")
            raise TelegramDeliveryError(
                f"Invio Telegram fallito ({response.status_code}): {description}"
            )

        result = body.get("result") or {}
        message_id = result.get("message_id")
        result_chat_id = (result.get("chat") or {}).get("id", chat_id)
        if message_id is None:
            raise TelegramDeliveryError(
                "Risposta Telegram valida ma priva di message_id"
            )

        return TelegramMessageResult(
            message_id=int(message_id),
            chat_id=result_chat_id,
            raw=body,
        )

    def _endpoint(self, method: str) -> str:
        return (
            f"{TELEGRAM_API_BASE_URL}/bot"
            f"{self.config.bot_token}/{method}"
        )


def _required_setting(name: str) -> str:
    value = str(settings.get(name, "")).strip()
    if not value:
        raise ValueError(f"{name} non configurato")
    return value


def _validated_chunk_size(value: object) -> int:
    chunk_size = int(value)
    if not 1 <= chunk_size <= TELEGRAM_TEXT_LIMIT:
        raise ValueError(
            "Telegram chunk_size deve essere compreso tra "
            f"1 e {TELEGRAM_TEXT_LIMIT}"
        )
    return chunk_size


def _as_bool(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _split_message(message: str, limit: int) -> list[str]:
    if len(message) <= limit:
        return [message]

    chunks: list[str] = []
    remaining = message
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, limit + 1)
        if split_at <= 0:
            split_at = limit

        chunk = remaining[:split_at].rstrip()
        if not chunk:
            chunk = remaining[:limit]
            split_at = limit

        chunks.append(chunk)
        remaining = remaining[split_at:].lstrip()

    return chunks
