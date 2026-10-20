from config.settings import settings
from notifications.providers.telegram import TelegramClient, TelegramConfig


def _required_setting(name: str) -> str:
    value = str(settings.get(name, "")).strip()
    if not value:
        raise ValueError(f"{name} is not configured")
    return value


def _as_bool(name: str, default: bool) -> bool:
    value = settings.get(name, str(default))
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    client = TelegramClient(
        TelegramConfig(
            bot_token=_required_setting("TELEGRAM_BOT_TOKEN"),
            chat_id=_required_setting("TELEGRAM_CHAT_ID"),
            timeout_seconds=float(
                settings.get("NOTIFICATIONS_HTTP_TIMEOUT_SECONDS", 30.0)
            ),
            parse_mode=(str(settings.get("TELEGRAM_PARSE_MODE", "")).strip() or None),
            disable_web_page_preview=_as_bool(
                "TELEGRAM_DISABLE_WEB_PAGE_PREVIEW",
                True,
            ),
            disable_notification=_as_bool(
                "TELEGRAM_DISABLE_NOTIFICATION",
                False,
            ),
            chunk_size=int(settings.get("TELEGRAM_MESSAGE_CHUNK_SIZE", 4000)),
        )
    )
    results = client.send("MIP platform test: administrator Telegram provider OK")

    for result in results:
        print(
            "Telegram admin OK: "
            f"chat_id={result.chat_id} "
            f"message_id={result.message_id}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
