from typing import Any

import httpx

from notifications.models import NotificationDelivery, NotificationSendResult


TELEGRAM_API_BASE_URL = "https://api.telegram.org"
TELEGRAM_TEXT_LIMIT = 4096
DEFAULT_CHUNK_SIZE = 4000


class TelegramPlugin:
    channel = "TELEGRAM"

    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        self.timeout_seconds = timeout_seconds

    def send(
        self,
        delivery: NotificationDelivery,
    ) -> NotificationSendResult:
        config = delivery.source_config
        token = str(
            config.get("bot_token")
            or config.get("token")
            or ""
        ).strip()
        chat_id = str(
            config.get("chat_id")
            or delivery.source_external_id
            or ""
        ).strip()

        if not token:
            raise ValueError("Telegram bot_token is missing in user_sources")
        if not chat_id:
            raise ValueError("Telegram chat_id is missing in user_sources")

        chunk_size = int(config.get("chunk_size", DEFAULT_CHUNK_SIZE))
        if not 1 <= chunk_size <= TELEGRAM_TEXT_LIMIT:
            raise ValueError(
                f"Telegram chunk_size must be between 1 and {TELEGRAM_TEXT_LIMIT}"
            )

        parse_mode = str(config.get("parse_mode") or "").strip() or None
        disable_preview = _as_bool(
            config.get("disable_web_page_preview", True)
        )
        disable_notification = _as_bool(
            config.get("disable_notification", False)
        )

        responses: list[dict[str, Any]] = []
        message_ids: list[str] = []

        with httpx.Client(timeout=self.timeout_seconds) as client:
            for chunk in _split_message(delivery.message, chunk_size):
                payload: dict[str, Any] = {
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": disable_preview,
                    "disable_notification": disable_notification,
                }
                if parse_mode:
                    payload["parse_mode"] = parse_mode

                response = client.post(
                    f"{TELEGRAM_API_BASE_URL}/bot{token}/sendMessage",
                    json=payload,
                )
                body = _telegram_response(response)
                result = body.get("result") or {}
                message_id = result.get("message_id")
                if message_id is None:
                    raise RuntimeError(
                        "Telegram response does not contain message_id"
                    )
                responses.append(body)
                message_ids.append(str(message_id))

        return NotificationSendResult(
            provider_message_id=",".join(message_ids),
            raw={"responses": responses},
        )


def _telegram_response(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        response.raise_for_status()
        raise RuntimeError("Telegram returned a non-JSON response") from exc

    if response.is_error or not body.get("ok"):
        description = body.get("description", "unknown Telegram error")
        raise RuntimeError(
            f"Telegram delivery failed ({response.status_code}): {description}"
        )
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
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    return chunks
