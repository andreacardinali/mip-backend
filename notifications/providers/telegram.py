from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from http_client import (
    HttpClient,
    HttpResponse,
    HttpStatusError,
    RetryPolicy,
    create_http_client,
    request_with_retry,
)
from notifications.credentials import decrypt_credential
from notifications.models import NotificationDelivery, NotificationSendResult

TELEGRAM_API_BASE_URL = "https://api.telegram.org"
TELEGRAM_TEXT_LIMIT = 4096
DEFAULT_CHUNK_SIZE = 4000


class TelegramDeliveryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    bot_token: str
    chat_id: str
    timeout_seconds: float = 30.0
    parse_mode: str | None = None
    disable_web_page_preview: bool = True
    disable_notification: bool = False
    chunk_size: int = DEFAULT_CHUNK_SIZE

    def __post_init__(self) -> None:
        if not self.bot_token.strip():
            raise ValueError("Telegram bot_token is missing")
        if not self.chat_id.strip():
            raise ValueError("Telegram chat_id is missing")
        if self.timeout_seconds <= 0:
            raise ValueError("Telegram timeout_seconds must be > 0")
        if not 1 <= self.chunk_size <= TELEGRAM_TEXT_LIMIT:
            raise ValueError(
                f"Telegram chunk_size must be between 1 and {TELEGRAM_TEXT_LIMIT}"
            )

    @classmethod
    def from_delivery(
        cls,
        delivery: NotificationDelivery,
        *,
        timeout_seconds: float,
    ) -> TelegramConfig:
        config = delivery.source_config
        encrypted = str(config.get("credential_encrypted") or "").strip()
        legacy = str(config.get("bot_token") or config.get("token") or "").strip()
        token = decrypt_credential(encrypted) if encrypted else legacy
        return cls(
            bot_token=token,
            chat_id=str(
                config.get("chat_id") or delivery.source_external_id or ""
            ).strip(),
            timeout_seconds=timeout_seconds,
            parse_mode=str(config.get("parse_mode") or "").strip() or None,
            disable_web_page_preview=_as_bool(
                config.get("disable_web_page_preview", True)
            ),
            disable_notification=_as_bool(config.get("disable_notification", False)),
            chunk_size=int(config.get("chunk_size", DEFAULT_CHUNK_SIZE)),
        )


@dataclass(frozen=True, slots=True)
class TelegramMessageResult:
    message_id: int
    chat_id: int | str
    raw: dict[str, Any]


class TelegramClient:
    def __init__(
        self,
        config: TelegramConfig,
        *,
        client: HttpClient | None = None,
    ) -> None:
        self.config = config
        self._client = client

    def send(self, message: str) -> list[TelegramMessageResult]:
        owns_client = self._client is None
        client = self._client or create_http_client(timeout=self.config.timeout_seconds)
        try:
            return [
                self._send_chunk(client, chunk)
                for chunk in _split_message(message, self.config.chunk_size)
            ]
        finally:
            if owns_client:
                client.close()

    def _send_chunk(self, client: HttpClient, text: str) -> TelegramMessageResult:
        payload: dict[str, Any] = {
            "chat_id": self.config.chat_id,
            "text": text,
            "disable_web_page_preview": self.config.disable_web_page_preview,
            "disable_notification": self.config.disable_notification,
        }
        if self.config.parse_mode:
            payload["parse_mode"] = self.config.parse_mode

        try:
            response = request_with_retry(
                client,
                "POST",
                f"{TELEGRAM_API_BASE_URL}/bot{self.config.bot_token}/sendMessage",
                policy=RetryPolicy(),
                retry_non_idempotent=False,
                sensitive_values=(self.config.bot_token,),
                json=payload,
            )
        except HttpStatusError as error:
            body = _telegram_response(error.response, status_error=error)
        else:
            body = _telegram_response(response)
        result = body.get("result") or {}
        message_id = result.get("message_id")
        if message_id is None:
            raise TelegramDeliveryError("Telegram response does not contain message_id")
        return TelegramMessageResult(
            message_id=int(message_id),
            chat_id=(result.get("chat") or {}).get("id", self.config.chat_id),
            raw=body,
        )


class TelegramProvider:
    channel = "TELEGRAM"

    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        self.timeout_seconds = timeout_seconds

    def send(self, delivery: NotificationDelivery) -> NotificationSendResult:
        client = TelegramClient(
            TelegramConfig.from_delivery(
                delivery,
                timeout_seconds=self.timeout_seconds,
            )
        )
        results = client.send(delivery.message)
        return NotificationSendResult(
            provider_message_id=",".join(str(result.message_id) for result in results),
            raw={"responses": [result.raw for result in results]},
        )


def _telegram_response(
    response: HttpResponse, *, status_error: HttpStatusError | None = None
) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        if status_error is not None:
            raise status_error from exc
        response.raise_for_status()
        raise TelegramDeliveryError("Telegram returned a non-JSON response") from exc
    if response.is_error or not body.get("ok"):
        description = body.get("description", "unknown Telegram error")
        raise TelegramDeliveryError(
            f"Telegram delivery failed ({response.status_code}): {description}"
        ) from status_error
    return body


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _split_message(message: str, limit: int) -> list[str]:
    text = message.strip()
    if not text:
        raise ValueError("Notification message cannot be empty")
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
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
