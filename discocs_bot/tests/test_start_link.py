from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.pop("bot", None)

from bot.handlers import start as start_module
from bot.services.discocs import DiscocsClient, DiscocsError


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str, **_kwargs) -> None:
        self.replies.append(text)


class FakeDiscocs:
    def __init__(self, result: str | None | Exception) -> None:
        self._result = result
        self.calls: list[tuple[str, int, str | None]] = []

    async def redeem_link(self, token: str, *, telegram_user_id: int, telegram_username: str | None):
        self.calls.append((token, telegram_user_id, telegram_username))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _run_start(args: list[str], discocs: FakeDiscocs) -> FakeMessage:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=555, username="alice_tg"),
        effective_message=message,
        callback_query=None,
    )
    context = SimpleNamespace(args=args, bot_data={"discocs": discocs, "settings": object()})
    asyncio.run(start_module.start_command(update, context))
    return message


def test_deep_link_redeems_token_with_the_senders_telegram_identity():
    discocs = FakeDiscocs("infected2202")

    message = _run_start(["link_abcDEF123_-"], discocs)

    assert discocs.calls == [("abcDEF123_-", 555, "alice_tg")]
    assert message.replies == [start_module.LINK_OK.format(username="infected2202")]


def test_stale_link_and_backend_failure_get_distinct_answers():
    assert _run_start(["link_old"], FakeDiscocs(None)).replies == [start_module.LINK_INVALID]
    assert _run_start(["link_x"], FakeDiscocs(DiscocsError("down"))).replies == [
        start_module.LINK_FAILED
    ]


def test_plain_start_shows_help_and_does_not_touch_discocs():
    discocs = FakeDiscocs("unused")

    assert _run_start([], discocs).replies == [start_module.HELP_TEXT]
    assert _run_start(["something_else"], discocs).replies == [start_module.HELP_TEXT]
    assert discocs.calls == []


def _client(handler) -> DiscocsClient:
    settings = SimpleNamespace(
        discocs_base_url="http://backend:7752",
        discocs_service_token="svc-secret",
        discocs_count=10,
    )
    client = DiscocsClient(settings, navidrome=None)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"X-Discocs-Service-Token": "svc-secret"},
    )
    return client


def test_redeem_link_client_contract():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        token = request.read().decode()
        if '"good"' in token:
            return httpx.Response(200, json={"username": "infected2202"})
        if '"short"' in token:
            return httpx.Response(422, json={"detail": "bad token"})
        if '"boom"' in token:
            return httpx.Response(500, json={"detail": "boom"})
        return httpx.Response(404, json={"error": {"code": "link_token_invalid"}})

    client = _client(handler)

    async def scenario() -> None:
        assert await client.redeem_link("good", telegram_user_id=5, telegram_username="u") == "infected2202"
        assert await client.redeem_link("stale", telegram_user_id=5, telegram_username=None) is None
        assert await client.redeem_link("short", telegram_user_id=5, telegram_username=None) is None
        try:
            await client.redeem_link("boom", telegram_user_id=5, telegram_username=None)
        except DiscocsError:
            pass
        else:
            raise AssertionError("5xx must surface as DiscocsError")
        await client.close()

    asyncio.run(scenario())
    assert seen[0].url == "http://backend:7752/api/v1/telegram/link/redeem"
    assert seen[0].headers["X-Discocs-Service-Token"] == "svc-secret"
