from notifications.telegram import TelegramSender


def main() -> int:
    sender = TelegramSender.for_platform_admin()
    results = sender.send("MIP platform test: administrator Telegram sender OK")

    for result in results:
        print(
            "Telegram admin OK: "
            f"chat_id={result.chat_id} "
            f"message_id={result.message_id}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
