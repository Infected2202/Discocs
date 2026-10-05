from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer
from telegram.error import BadRequest, Forbidden

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.pop("bot", None)

from bot.services import internal_api
from bot.services.internal_api import (
    InternalApiDeps,
    SendRejected,
    build_internal_app,
    parse_send_request,
    send_item,
)
from bot.services.navidrome import NavidromeError
from bot.storage.models import Album, Track

TOKEN = "svc-secret"


class FakeBot:
    username = "discocs_bot"

    def __init__(self, *, fail_with: list[Exception] | None = None) -> None:
        self.photos: list[dict] = []
        self._failures = list(fail_with or [])

    async def send_photo(self, *, chat_id, photo, caption, reply_markup=None):
        if self._failures:
            raise self._failures.pop(0)
        self.photos.append({"chat_id": chat_id, "caption": caption, "reply_markup": reply_markup})
        return SimpleNamespace(chat_id=chat_id, message_id=len(self.photos), photo=[object()])


class FakeNavidrome:
    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error

    async def get_song(self, song_id: str) -> Track:
        if self._error:
            raise self._error
        return Track(id=song_id, title="Song", artist="Artist", album="Record", album_id="al-1")

    async def get_album(self, album_id: str):
        if self._error:
            raise self._error
        album = Album(id=album_id, title="Record", artist="Artist", year=1999, track_count=9)
        return album, []

    async def download_cover_art(self, *_args, **_kwargs) -> bool:
        return False


class FakeDelivery:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def deliver_track_to_message(self, bot, *, chat_id, message_id, song_id, user_id):
        self.calls.append(
            {"chat_id": chat_id, "message_id": message_id, "song_id": song_id, "user_id": user_id}
        )


def _deps(tmp_path: Path, *, bot=None, navidrome=None) -> tuple[InternalApiDeps, list]:
    spawned: list = []
    deps = InternalApiDeps(
        bot=bot or FakeBot(),
        navidrome=navidrome or FakeNavidrome(),
        delivery=FakeDelivery(),
        temp_dir=tmp_path / "tmp",
        spawn=spawned.append,
    )
    return deps, spawned


def _buttons(markup) -> list[tuple[str, str | None, str | None]]:
    return [
        (button.text, button.callback_data, button.url)
        for row in markup.inline_keyboard
        for button in row
    ]


def test_track_send_posts_loading_card_and_delivers_into_it_in_background(tmp_path):
    deps, spawned = _deps(tmp_path)

    status = asyncio.run(
        send_item(deps, {"telegram_user_id": 555, "kind": "track", "navidrome_id": "nd-song"})
    )

    assert status == "queued"
    assert [photo["chat_id"] for photo in deps.bot.photos] == [555]
    assert deps.delivery.calls == []  # audio is prepared after the web request returns

    assert len(spawned) == 1
    asyncio.run(spawned[0])
    # The loading card becomes the track, with the owner's own audio profile.
    assert deps.delivery.calls == [
        {"chat_id": 555, "message_id": 1, "song_id": "nd-song", "user_id": 555}
    ]


def test_release_send_posts_album_card_with_send_all_and_open_buttons(tmp_path):
    deps, spawned = _deps(tmp_path)

    status = asyncio.run(
        send_item(
            deps,
            {
                "telegram_user_id": 555,
                "kind": "release",
                "navidrome_id": "nd-album",
                "open_url": "https://music.example.com/releases/7",
            },
        )
    )

    assert status == "sent"
    assert spawned == []
    [photo] = deps.bot.photos
    assert photo["chat_id"] == 555
    assert photo["caption"].splitlines()[0] == "📀 Artist — Record"
    assert _buttons(photo["reply_markup"]) == [
        ("📀 Отправить все треки", "album:nd-album", None),
        ("🌐 Открыть в discocs", None, "https://music.example.com/releases/7"),
    ]


def test_album_card_drops_open_button_when_telegram_rejects_the_url(tmp_path):
    bot = FakeBot(fail_with=[BadRequest("Wrong http url")])
    deps, _ = _deps(tmp_path, bot=bot)

    asyncio.run(
        send_item(
            deps,
            {
                "telegram_user_id": 555,
                "kind": "release",
                "navidrome_id": "nd-album",
                "open_url": "http://192.168.1.41/releases/7",
            },
        )
    )

    [photo] = bot.photos
    assert _buttons(photo["reply_markup"]) == [("📀 Отправить все треки", "album:nd-album", None)]


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        (Forbidden("bot was blocked by the user"), 409, "chat_unavailable"),
        (BadRequest("Chat not found"), 409, "chat_unavailable"),
        (BadRequest("Something else"), 502, "telegram_error"),
    ],
)
def test_telegram_failures_are_reported_to_the_backend(tmp_path, failure, status, code):
    deps, spawned = _deps(tmp_path, bot=FakeBot(fail_with=[failure]))

    with pytest.raises(SendRejected) as rejected:
        asyncio.run(
            send_item(deps, {"telegram_user_id": 555, "kind": "track", "navidrome_id": "nd-song"})
        )

    assert (rejected.value.status, rejected.value.code) == (status, code)
    assert spawned == []


def test_navidrome_failure_is_reported_without_touching_the_chat(tmp_path):
    deps, _ = _deps(tmp_path, navidrome=FakeNavidrome(error=NavidromeError("down")))

    with pytest.raises(SendRejected) as rejected:
        asyncio.run(
            send_item(deps, {"telegram_user_id": 555, "kind": "release", "navidrome_id": "nd-album"})
        )

    assert (rejected.value.status, rejected.value.code) == (502, "navidrome_unavailable")
    assert deps.bot.photos == []


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"telegram_user_id": True, "kind": "track", "navidrome_id": "x"},
        {"telegram_user_id": 0, "kind": "track", "navidrome_id": "x"},
        {"telegram_user_id": 1, "kind": "playlist", "navidrome_id": "x"},
        {"telegram_user_id": 1, "kind": "track", "navidrome_id": ""},
        {"telegram_user_id": 1, "kind": "release", "navidrome_id": "x" * 49},
        {"telegram_user_id": 1, "kind": "release", "navidrome_id": "x", "open_url": "javascript:alert(1)"},
    ],
)
def test_malformed_send_requests_are_rejected(payload):
    with pytest.raises(SendRejected) as rejected:
        parse_send_request(payload)
    assert rejected.value.status == 400


def test_http_layer_requires_the_service_token(tmp_path):
    deps, spawned = _deps(tmp_path)
    body = {"telegram_user_id": 555, "kind": "track", "navidrome_id": "nd-song"}

    async def scenario() -> None:
        async with TestClient(TestServer(build_internal_app(deps, TOKEN))) as client:
            anonymous = await client.get("/internal/info")
            assert anonymous.status == 401
            wrong = await client.post(
                "/internal/send", json=body, headers={"X-Discocs-Service-Token": "nope"}
            )
            assert wrong.status == 401

            headers = {"X-Discocs-Service-Token": TOKEN}
            info = await client.get("/internal/info", headers=headers)
            assert await info.json() == {"username": "discocs_bot"}

            sent = await client.post("/internal/send", json=body, headers=headers)
            assert sent.status == 202
            assert await sent.json() == {"status": "queued"}

            bad = await client.post("/internal/send", data="not json", headers=headers)
            assert bad.status == 400

    asyncio.run(scenario())
    # Only the authorised request reached Telegram.
    assert [photo["chat_id"] for photo in deps.bot.photos] == [555]
    assert len(spawned) == 1
    spawned[0].close()


def test_internal_app_refuses_to_start_without_a_token(tmp_path):
    deps, _ = _deps(tmp_path)
    with pytest.raises(ValueError):
        build_internal_app(deps, "")


def test_run_bot_skips_internal_api_without_service_token(tmp_path):
    from bot import main as bot_main

    application = SimpleNamespace(
        bot_data={"settings": SimpleNamespace(discocs_service_token="")},
    )
    assert asyncio.run(bot_main._start_internal_api(application)) is None
    assert internal_api.SERVICE_TOKEN_HEADER == "X-Discocs-Service-Token"
