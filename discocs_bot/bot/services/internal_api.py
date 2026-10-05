"""Внутренний HTTP-эндпоинт бота для backend'а discocs (docs/telegram.md).

Telegram доступен только через awg-туннель, в неймспейсе которого живёт бот,
поэтому backend не ходит в Bot API сам: он просит бота, а бот отправляет своим
DeliveryService (транскод, кэш file_id, настройки качества пользователя).

Порт слушается внутри compose-сети (backend стучится в `awg:<port>`), снаружи
и со стороны туннеля закрыт (deploy/prod/start-awg.sh). Каждый запрос несёт
тот же DISCOCS_SERVICE_TOKEN, что бот шлёт backend'у; без токена сервер не
поднимается вовсе.
"""
from __future__ import annotations

import hmac
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from aiohttp import web
from telegram import Bot
from telegram.error import BadRequest, Forbidden, TelegramError

from bot.services.delivery import DeliveryService
from bot.services.navidrome import NavidromeClient, NavidromeError
from bot.utils.loading_delivery import LOADING_CAPTION, deliver_into_loading_card
from bot.utils.track_cards import send_album_card, send_track_loading_card

logger = logging.getLogger(__name__)

SERVICE_TOKEN_HEADER = "X-Discocs-Service-Token"
# callback_data у Telegram ограничен 64 байтами, а id уходит в «album:<id>».
MAX_NAVIDROME_ID_LENGTH = 48
SEND_KINDS = ("track", "release")


@dataclass
class InternalApiDeps:
    bot: Bot
    navidrome: NavidromeClient
    delivery: DeliveryService
    temp_dir: Any
    spawn: Callable[[Coroutine[Any, Any, None]], object]


class SendRejected(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def _error(status: int, code: str) -> web.Response:
    return web.json_response({"error": {"code": code}}, status=status)


def _is_chat_unavailable(exc: TelegramError) -> bool:
    # Forbidden — пользователь не запускал бота или заблокировал его;
    # «chat not found» — такого чата у бота нет.
    if isinstance(exc, Forbidden):
        return True
    return isinstance(exc, BadRequest) and "chat not found" in str(exc).lower()


def parse_send_request(payload: object) -> tuple[int, str, str, str | None]:
    if not isinstance(payload, dict):
        raise SendRejected(400, "invalid_request")
    chat_id = payload.get("telegram_user_id")
    kind = payload.get("kind")
    navidrome_id = payload.get("navidrome_id")
    open_url = payload.get("open_url")
    if not isinstance(chat_id, int) or isinstance(chat_id, bool) or chat_id <= 0:
        raise SendRejected(400, "invalid_request")
    if kind not in SEND_KINDS:
        raise SendRejected(400, "invalid_request")
    if (
        not isinstance(navidrome_id, str)
        or not navidrome_id
        or len(navidrome_id) > MAX_NAVIDROME_ID_LENGTH
    ):
        raise SendRejected(400, "invalid_request")
    if open_url is not None and (
        not isinstance(open_url, str) or not open_url.startswith(("https://", "http://"))
    ):
        raise SendRejected(400, "invalid_request")
    return chat_id, kind, navidrome_id, open_url


async def send_item(deps: InternalApiDeps, payload: object) -> str:
    """Отправить трек или карточку релиза в личный чат; вернуть статус.

    Синхронно делается только первое сообщение — оно же проверяет, что бот
    может писать в этот чат. Подготовка аудио идёт в фоне: веб не ждёт
    транскода, а карточка загрузки сама превращается в трек или в ошибку.
    """
    chat_id, kind, navidrome_id, open_url = parse_send_request(payload)
    deps.temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        if kind == "track":
            track = await deps.navidrome.get_song(navidrome_id)
            loading = await send_track_loading_card(
                deps.bot,
                chat_id,
                track,
                navidrome=deps.navidrome,
                temp_dir=deps.temp_dir,
                caption=LOADING_CAPTION,
            )
            deps.spawn(
                deliver_into_loading_card(
                    deps.bot, deps.delivery, loading, navidrome_id, user_id=chat_id
                )
            )
            return "queued"
        album, _tracks = await deps.navidrome.get_album(navidrome_id)
        await send_album_card(
            deps.bot,
            chat_id,
            album,
            navidrome=deps.navidrome,
            temp_dir=deps.temp_dir,
            open_url=open_url,
        )
        return "sent"
    except NavidromeError as exc:
        logger.warning("internal send: navidrome failed for %s %s: %s", kind, navidrome_id, exc)
        raise SendRejected(502, "navidrome_unavailable") from exc
    except TelegramError as exc:
        if _is_chat_unavailable(exc):
            logger.info("internal send: chat %s unavailable: %s", chat_id, exc)
            raise SendRejected(409, "chat_unavailable") from exc
        logger.warning("internal send: telegram failed for chat %s: %s", chat_id, exc)
        raise SendRejected(502, "telegram_error") from exc


def build_internal_app(deps: InternalApiDeps, service_token: str) -> web.Application:
    if not service_token:
        raise ValueError("internal API requires DISCOCS_SERVICE_TOKEN")

    @web.middleware
    async def require_service_token(request: web.Request, handler):
        presented = request.headers.get(SERVICE_TOKEN_HEADER, "")
        if not presented or not hmac.compare_digest(presented.encode(), service_token.encode()):
            return _error(401, "unauthorized")
        return await handler(request)

    async def info(_request: web.Request) -> web.Response:
        return web.json_response({"username": deps.bot.username})

    async def send(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except ValueError:
            return _error(400, "invalid_request")
        try:
            status = await send_item(deps, payload)
        except SendRejected as exc:
            return _error(exc.status, exc.code)
        return web.json_response({"status": status}, status=202 if status == "queued" else 200)

    app = web.Application(middlewares=[require_service_token], client_max_size=64 * 1024)
    app.router.add_get("/internal/info", info)
    app.router.add_post("/internal/send", send)
    return app


class InternalApiServer:
    def __init__(self, app: web.Application, *, host: str, port: int) -> None:
        self._runner = web.AppRunner(app, access_log=None)
        self._host = host
        self._port = port

    async def start(self) -> None:
        await self._runner.setup()
        await web.TCPSite(self._runner, self._host, self._port).start()
        logger.info("Internal API listening on %s:%s", self._host, self._port)

    async def stop(self) -> None:
        await self._runner.cleanup()
