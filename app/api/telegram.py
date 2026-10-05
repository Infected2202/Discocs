"""Telegram bot integration: account linking and "send to my Telegram".

User endpoints need a signed-in session; ``/telegram/link/redeem`` is the
bot's half of the deep-link handshake and accepts only the service principal.
The backend never calls the Telegram Bot API itself — see app/telegram_bot.py
and docs/telegram.md.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import api_error, context
from app.api.shares import public_origin
from app.audio_source import NAVIDROME_PROVIDER, navidrome_item_id_for_track
from app.telegram_bot import (
    TelegramBotRejected,
    TelegramBotUnavailable,
    bot_username,
    send_to_bot,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/telegram")

# Mirrors the token minted in app/store/telegram.py (token_urlsafe(24)).
_LINK_TOKEN_PATTERN = r"^[A-Za-z0-9_-]{16,64}$"


class LinkRedeemRequest(BaseModel):
    token: str = Field(pattern=_LINK_TOKEN_PATTERN)
    telegram_user_id: int = Field(gt=0)
    telegram_username: str | None = Field(default=None, max_length=64)

    model_config = ConfigDict(extra="forbid")


class SendRequest(BaseModel):
    source_type: str = Field(pattern="^(track|release)$")
    source_id: int = Field(gt=0)

    model_config = ConfigDict(extra="forbid")


def _signed_in(request: Request) -> bool:
    principal = str(getattr(request.state, "principal", ""))
    return principal != "service" and isinstance(getattr(request.state, "user_id", None), int)


def _is_service(request: Request) -> bool:
    return str(getattr(request.state, "principal", "")) == "service"


def _session_required() -> JSONResponse:
    return api_error(401, "unauthorized", "User session required")


def _bot_unavailable() -> JSONResponse:
    return api_error(503, "telegram_bot_unavailable", "Telegram bot is unavailable")


def _link_dict(row) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return {
        "telegram_username": row["telegram_username"],
        "linked_at": row["linked_at"],
    }


@router.get("/link", response_model=None)
def get_link(request: Request) -> dict[str, object] | JSONResponse:
    if not _signed_in(request):
        return _session_required()
    store, settings = context()
    row = store.get_own_telegram_link()
    return {
        "enabled": settings.telegram.enabled,
        "linked": row is not None,
        "link": _link_dict(row) if row is not None else None,
    }


@router.post("/link", response_model=None)
def start_link(request: Request) -> dict[str, object] | JSONResponse:
    if not _signed_in(request):
        return _session_required()
    store, settings = context()
    if not settings.telegram.enabled:
        return api_error(404, "telegram_disabled", "Telegram integration is not configured")
    try:
        username = bot_username(settings)
    except (TelegramBotUnavailable, TelegramBotRejected):
        logger.warning("Telegram bot did not answer /internal/info", exc_info=True)
        return _bot_unavailable()
    token, expires_at = store.create_telegram_link_token(
        ttl_minutes=settings.telegram.link_ttl_minutes
    )
    return {"url": f"https://t.me/{username}?start=link_{token}", "expires_at": expires_at}


@router.delete("/link", status_code=204, response_model=None)
def delete_link(request: Request) -> Response | JSONResponse:
    if not _signed_in(request):
        return _session_required()
    store, _settings = context()
    store.delete_own_telegram_link()
    return Response(status_code=204)


@router.post("/link/redeem", response_model=None)
def redeem_link(request: Request, payload: LinkRedeemRequest) -> dict[str, object] | JSONResponse:
    if not _is_service(request):
        return api_error(403, "forbidden", "Service principal required")
    store, _settings = context()
    username = store.redeem_telegram_link_token(
        payload.token,
        telegram_user_id=payload.telegram_user_id,
        telegram_username=payload.telegram_username,
    )
    if username is None:
        return api_error(404, "link_token_invalid", "Link is unknown or expired")
    return {"username": username}


@router.post("/send", status_code=202, response_model=None)
def send(request: Request, payload: SendRequest) -> dict[str, object] | JSONResponse:
    if not _signed_in(request):
        return _session_required()
    store, settings = context()
    if not settings.telegram.enabled:
        return api_error(404, "telegram_disabled", "Telegram integration is not configured")
    link = store.get_own_telegram_link()
    if link is None:
        return api_error(409, "telegram_not_linked", "Telegram account is not linked")

    bot_payload: dict[str, object] = {"telegram_user_id": int(link["telegram_user_id"])}
    if payload.source_type == "track":
        track = store.get_track(payload.source_id)
        if track is None:
            return api_error(404, "not_found", "Track not found")
        navidrome_id = navidrome_item_id_for_track(store, track)
        bot_payload.update(kind="track", navidrome_id=navidrome_id)
    else:
        if store.get_release(payload.source_id) is None:
            return api_error(404, "not_found", "Release not found")
        navidrome_id = store.external_id_for_entity(
            NAVIDROME_PROVIDER, "release", payload.source_id
        )
        bot_payload.update(
            kind="release",
            navidrome_id=navidrome_id,
            open_url=f"{public_origin(request)}/releases/{payload.source_id}",
        )
    if not navidrome_id:
        return api_error(422, "not_in_navidrome", "Only Navidrome items can be sent to Telegram")

    try:
        send_to_bot(settings, bot_payload)
    except TelegramBotRejected as exc:
        if exc.code == "chat_unavailable":
            return api_error(409, "telegram_chat_unavailable", "The bot cannot write to this chat")
        logger.warning("Telegram bot rejected send: %s", exc.code)
        return api_error(502, "telegram_send_failed", "Telegram bot could not send the item")
    except TelegramBotUnavailable:
        logger.warning("Telegram bot unreachable on send", exc_info=True)
        return _bot_unavailable()
    return {"status": "sent"}
