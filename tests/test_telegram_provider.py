import json
import logging

import pytest

from http_client import HttpStatusError, get_http_status
from http_client import testing as httpx

telegram = pytest.importorskip(
    "notifications.providers.telegram", exc_type=ModuleNotFoundError
)
TelegramClient = telegram.TelegramClient
TelegramConfig = telegram.TelegramConfig
TelegramDeliveryError = telegram.TelegramDeliveryError

BOT_TOKEN = "123456:test-secret"


def test_telegram_sends_chunks_and_preserves_injected_client():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": len(requests)}},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = TelegramClient(
            TelegramConfig(BOT_TOKEN, "chat", message_thread_id=7, parse_mode="HTML"),
            client=client,
            max_message_length=4,
        )
        results = provider.send("abcdefgh")
        assert [result.message_id for result in results] == [1, 2]
        assert not client.is_closed

    assert [request.method for request in requests] == ["POST", "POST"]
    assert requests[0].url.path == f"/bot{BOT_TOKEN}/sendMessage"
    assert [json.loads(request.content)["text"] for request in requests] == [
        "abcd",
        "efgh",
    ]
    assert json.loads(requests[0].content) == {
        "chat_id": "chat",
        "text": "abcd",
        "disable_web_page_preview": True,
        "message_thread_id": 7,
        "parse_mode": "HTML",
    }


@pytest.mark.parametrize("status", [400, 403, 429, 503])
def test_telegram_preserves_json_delivery_errors_without_retry(status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            status, json={"ok": False, "description": "delivery denied"}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = TelegramClient(TelegramConfig(BOT_TOKEN, "chat"), client=client)
        with pytest.raises(
            TelegramDeliveryError, match=f"{status}.*delivery denied"
        ) as captured:
            provider.send("test")
        assert get_http_status(captured.value) == status
        assert not client.is_closed
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"ok": False, "description": "denied"}, "denied"),
        ({"ok": True, "result": {}}, "missing message_id"),
    ],
)
def test_telegram_preserves_application_errors(body, message):
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    ) as client:
        provider = TelegramClient(TelegramConfig(BOT_TOKEN, "chat"), client=client)
        with pytest.raises(TelegramDeliveryError, match=message):
            provider.send("test")


@pytest.mark.parametrize("status", [200, 403, 503])
def test_telegram_non_json_errors_do_not_expose_bot_token(status, caplog):
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, text="invalid")
        )
    ) as client:
        provider = TelegramClient(TelegramConfig(BOT_TOKEN, "chat"), client=client)
        error_type = TelegramDeliveryError if status == 200 else HttpStatusError
        with caplog.at_level(logging.INFO, logger="httpx"):
            with pytest.raises(error_type) as captured:
                provider.send("test")
        assert BOT_TOKEN not in str(captured.value)
        assert BOT_TOKEN not in caplog.text
        if status != 200:
            assert get_http_status(captured.value) == status


def test_telegram_closes_owned_client_on_error(monkeypatch):
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(503, json={"ok": False})
        )
    )
    monkeypatch.setattr(telegram, "create_http_client", lambda **kwargs: client)
    provider = TelegramClient(TelegramConfig(BOT_TOKEN, "chat"))
    with pytest.raises(TelegramDeliveryError):
        provider.send("test")
    assert client.is_closed
