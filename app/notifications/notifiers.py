"""Notification interfaces and implementations."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.ssrf import UnsafeURLError, ensure_safe_url

logger = logging.getLogger(__name__)


class Notifier(ABC):
    """Base interface for incident notifications."""

    @abstractmethod
    async def send(self, subject: str, message: str, **meta: Any) -> bool:
        """Deliver a notification. Returns True on success."""


class TelegramNotifier(Notifier):
    """Sends notifications through the Telegram Bot API."""

    def __init__(self, token: str, chat_id: str, client: httpx.AsyncClient | None = None) -> None:
        self.token = token
        self.chat_id = chat_id
        self._client = client

    async def send(self, subject: str, message: str, **meta: Any) -> bool:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": f"{subject}\n\n{message}"}
        client = self._client or httpx.AsyncClient(timeout=10.0)
        try:
            response = await client.post(url, json=payload)
            if response.status_code >= 400:
                logger.warning(
                    "telegram_notification_failed", extra={"status": response.status_code}
                )
                return False
            return True
        except httpx.HTTPError:
            logger.exception("telegram_notification_error")
            return False
        finally:
            if self._client is None:
                await client.aclose()


class WebhookNotifier(Notifier):
    """Posts JSON payloads to a user-provided webhook URL."""

    def __init__(self, url: str, client: httpx.AsyncClient | None = None) -> None:
        self.url = url
        self._client = client

    async def send(self, subject: str, message: str, **meta: Any) -> bool:
        payload = {"subject": subject, "message": message, **meta}
        client = self._client or httpx.AsyncClient(timeout=10.0)
        try:
            response = await client.post(self.url, json=payload)
            if response.status_code >= 400:
                logger.warning(
                    "webhook_notification_failed", extra={"status": response.status_code}
                )
                return False
            return True
        except httpx.HTTPError:
            logger.exception("webhook_notification_error")
            return False
        finally:
            if self._client is None:
                await client.aclose()


class NotificationDispatcher:
    """Fans a notification out to every configured channel."""

    def __init__(self, notifiers: list[Notifier] | None = None) -> None:
        self.notifiers = notifiers or []

    async def dispatch(
        self, subject: str, message: str, webhook_url: str | None = None, **meta: Any
    ) -> None:
        targets = list(self.notifiers)
        if webhook_url:
            try:
                await ensure_safe_url(webhook_url)
            except UnsafeURLError:
                logger.warning("webhook_target_rejected")
            else:
                targets.append(WebhookNotifier(webhook_url))
        for notifier in targets:
            try:
                await notifier.send(subject, message, **meta)
            except Exception:  # noqa: BLE001 - notifications must never break checks
                logger.exception("notification_dispatch_error")


def build_default_dispatcher() -> NotificationDispatcher:
    """Create a dispatcher from application settings."""
    settings = get_settings()
    notifiers: list[Notifier] = []
    if settings.telegram_token and settings.telegram_chat_id:
        notifiers.append(TelegramNotifier(settings.telegram_token, settings.telegram_chat_id))
    return NotificationDispatcher(notifiers)
