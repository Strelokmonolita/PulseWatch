"""Notification channel tests."""

import json
from typing import Any

import httpx

from app.notifications import notifiers
from app.notifications.notifiers import (
    NotificationDispatcher,
    TelegramNotifier,
    WebhookNotifier,
    build_default_dispatcher,
)


async def test_webhook_notifier_posts_payload() -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        notifier = WebhookNotifier("https://hooks.example.com/x", client=client)
        assert await notifier.send("subject", "message", monitor_id=7) is True

    assert captured[0]["subject"] == "subject"
    assert captured[0]["monitor_id"] == 7


async def test_webhook_notifier_reports_failure() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500))
    ) as client:
        notifier = WebhookNotifier("https://hooks.example.com/x", client=client)
        assert await notifier.send("subject", "message") is False


async def test_webhook_notifier_survives_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        notifier = WebhookNotifier("https://hooks.example.com/x", client=client)
        assert await notifier.send("subject", "message") is False


async def test_telegram_notifier_uses_bot_api() -> None:
    urls: list[str] = []
    payloads: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        notifier = TelegramNotifier("token123", "chat42", client=client)
        assert await notifier.send("subject", "message") is True

    assert urls[0].endswith("/bottoken123/sendMessage")
    assert payloads[0]["chat_id"] == "chat42"


async def test_dispatcher_survives_failing_notifier() -> None:
    class Broken:
        async def send(self, subject: str, message: str, **meta: Any) -> bool:
            raise RuntimeError("boom")

    received: list[str] = []

    class Working:
        async def send(self, subject: str, message: str, **meta: Any) -> bool:
            received.append(subject)
            return True

    dispatcher = NotificationDispatcher([Broken(), Working()])  # type: ignore[list-item]
    await dispatcher.dispatch("subject", "message", webhook_url=None)
    assert received == ["subject"]


async def test_dispatcher_creates_webhook_notifier_for_monitor(
    monkeypatch: Any,
) -> None:
    created: list[str] = []

    class FakeWebhook:
        def __init__(self, url: str) -> None:
            created.append(url)

        async def send(self, subject: str, message: str, **meta: Any) -> bool:
            return True

    async def allow(_url: str) -> None:
        return None

    monkeypatch.setattr(notifiers, "ensure_safe_url", allow)
    monkeypatch.setattr(notifiers, "WebhookNotifier", FakeWebhook)
    dispatcher = NotificationDispatcher([])
    await dispatcher.dispatch("s", "m", webhook_url="https://hooks.example.com/incident")
    assert created == ["https://hooks.example.com/incident"]


async def test_dispatcher_rejects_unsafe_webhook(monkeypatch: Any) -> None:
    created: list[str] = []

    class FakeWebhook:
        def __init__(self, url: str) -> None:
            created.append(url)

        async def send(self, subject: str, message: str, **meta: Any) -> bool:
            return True

    monkeypatch.setattr(notifiers, "WebhookNotifier", FakeWebhook)
    dispatcher = NotificationDispatcher([])
    await dispatcher.dispatch("s", "m", webhook_url="http://127.0.0.1:9000/hook")
    assert created == []


def test_default_dispatcher_without_telegram_settings() -> None:
    dispatcher = build_default_dispatcher()
    assert dispatcher.notifiers == []
