"""Client for the Telegram bot's internal HTTP endpoint (docs/telegram.md).

The bot lives in the awg container's network namespace because Telegram is
reachable only through that tunnel, so the backend never talks to the Bot API
itself: it hands the bot a request and the bot does the delivery with its own
transcoding and ``file_id`` cache.
"""
from __future__ import annotations

import threading
from typing import Any

import httpx

from app.config import Settings

_TIMEOUT = httpx.Timeout(20.0, connect=3.0)
_USERNAME_CACHE: dict[str, str] = {}
_USERNAME_LOCK = threading.Lock()


class TelegramBotUnavailable(Exception):
    """The bot is not configured, not running or did not answer."""


class TelegramBotRejected(Exception):
    """The bot answered with an error ``code`` (e.g. ``chat_unavailable``)."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def _request(settings: Settings, method: str, path: str, *, json: dict[str, Any] | None = None) -> dict[str, Any]:
    if not settings.telegram.enabled:
        raise TelegramBotUnavailable("DISCOCS_BOT_URL is not set")
    headers = {"X-Discocs-Service-Token": settings.auth.service_token}
    try:
        response = httpx.request(
            method,
            f"{settings.telegram.bot_url}{path}",
            json=json,
            headers=headers,
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise TelegramBotUnavailable(str(exc)) from exc
    if response.status_code >= 500 or response.status_code in {401, 403}:
        raise TelegramBotUnavailable(f"bot answered HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise TelegramBotUnavailable("bot answered with non-JSON body") from exc
    if response.status_code >= 400:
        error = payload.get("error") if isinstance(payload, dict) else None
        code = str(error.get("code")) if isinstance(error, dict) else "bot_error"
        raise TelegramBotRejected(code)
    return payload if isinstance(payload, dict) else {}


def bot_username(settings: Settings) -> str:
    """The bot's @username for deep links, cached per bot URL once known."""
    url = settings.telegram.bot_url
    with _USERNAME_LOCK:
        cached = _USERNAME_CACHE.get(url)
    if cached:
        return cached
    username = str(_request(settings, "GET", "/internal/info").get("username") or "")
    if not username:
        raise TelegramBotUnavailable("bot did not report its username")
    with _USERNAME_LOCK:
        _USERNAME_CACHE[url] = username
    return username


def send_to_bot(settings: Settings, payload: dict[str, Any]) -> None:
    _request(settings, "POST", "/internal/send", json=payload)


def clear_username_cache() -> None:
    with _USERNAME_LOCK:
        _USERNAME_CACHE.clear()
