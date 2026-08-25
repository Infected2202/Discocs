"""The chat flow for links: card first, download only when asked."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.pop("bot", None)

from bot.handlers import links as links_module
from bot.services.external_audio import ExternalAudioError, ExternalTrackInfo
from bot.utils.links import UnsafeLinkError


def info() -> ExternalTrackInfo:
    return ExternalTrackInfo(
        media_key="abc123",
        url_key="youtube:abc123",
        source="youtube",
        webpage_url="https://youtu.be/abc123",
        title="Xtal",
        artist="Aphex Twin",
        duration=293,
        thumbnail_url="https://img/hq.jpg",
    )


class FakeStatus:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.deleted = False
        self.message_id = 1

    async def edit_text(self, text: str) -> None:
        self.texts.append(text)

    async def delete(self) -> None:
        self.deleted = True


class FakeMessage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.caption = None
        self.chat_id = 42
        self.status = FakeStatus()
        self.photos: list[dict] = []
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> FakeStatus:
        self.replies.append(text)
        return self.status

    async def reply_photo(self, **kwargs) -> None:
        self.photos.append(kwargs)


class FakeDb:
    def __init__(self, media: dict | None = None, parts: list | None = None) -> None:
        self.media = media
        self.parts = parts or []
        self.saved: list[dict] = []
        self.events: list[str] = []
        self.touched: list[str] = []

    async def save_external_media(self, **kwargs) -> None:
        self.saved.append(kwargs)

    async def get_external_media(self, media_key: str):
        return self.media

    async def get_external_parts(self, media_key: str) -> list:
        return self.parts

    async def touch_external_media(self, media_key: str, now: str) -> None:
        self.touched.append(media_key)

    async def log_event(self, **kwargs) -> None:
        self.events.append(kwargs["event_type"])


def update_for(message: FakeMessage) -> SimpleNamespace:
    return SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=7),
        callback_query=None,
    )


def context_for(**bot_data) -> SimpleNamespace:
    data = {"settings": SimpleNamespace(temp_dir=Path("temp"))}
    data.update(bot_data)
    return SimpleNamespace(bot=SimpleNamespace(name="bot"), bot_data=data)


def allow_everyone(monkeypatch) -> None:
    async def allowed(_update, _context) -> bool:
        return False

    monkeypatch.setattr(links_module, "deny_if_not_allowed", allowed)


def test_card_caption_shows_source_artist_and_duration():
    assert links_module.card_caption(info()) == "🔗 youtube\nAphex Twin — Xtal\n4:53"


def test_format_duration_handles_hours():
    assert links_module.format_duration(7325) == "2:02:05"
    assert links_module.format_duration(None) == ""


def test_link_message_sends_a_card_and_remembers_the_media(monkeypatch):
    allow_everyone(monkeypatch)
    monkeypatch.setattr(links_module, "validate_public_url", lambda url: url)
    message = FakeMessage("глянь https://youtu.be/abc123")
    db = FakeDb()

    class FakeLinks:
        async def fetch_info(self, url: str) -> ExternalTrackInfo:
            assert url == "https://youtu.be/abc123"
            return info()

    context = context_for(links=FakeLinks(), db=db)
    asyncio.run(links_module.link_message_handler(update_for(message), context))

    assert message.status.deleted is True
    assert len(message.photos) == 1
    assert message.photos[0]["photo"] == "https://img/hq.jpg"
    assert "Aphex Twin — Xtal" in message.photos[0]["caption"]
    keyboard = message.photos[0]["reply_markup"].inline_keyboard
    assert keyboard[0][0].callback_data == "extget:abc123"
    assert db.saved[0]["url_key"] == "youtube:abc123"
    assert db.events == ["external_link"]


def test_link_to_an_internal_address_is_reported_and_not_fetched(monkeypatch):
    allow_everyone(monkeypatch)

    def refuse(url: str) -> str:
        raise UnsafeLinkError("blocked", user_message="Эта ссылка ведёт внутрь сети — не открываю.")

    monkeypatch.setattr(links_module, "validate_public_url", refuse)
    message = FakeMessage("http://192.168.1.41:8711/api/v1/settings")

    class ExplodingLinks:
        async def fetch_info(self, url: str):
            raise AssertionError("must not fetch a refused link")

    db = FakeDb()
    context = context_for(links=ExplodingLinks(), db=db)
    asyncio.run(links_module.link_message_handler(update_for(message), context))

    assert message.status.texts == ["Эта ссылка ведёт внутрь сети — не открываю."]
    assert db.saved == []
    assert message.photos == []


def test_unsupported_source_is_reported(monkeypatch):
    allow_everyone(monkeypatch)
    monkeypatch.setattr(links_module, "validate_public_url", lambda url: url)
    message = FakeMessage("https://example.com/track")

    class FakeLinks:
        async def fetch_info(self, url: str):
            raise ExternalAudioError("no extractor", user_message="Не знаю такой источник.")

    context = context_for(links=FakeLinks(), db=FakeDb())
    asyncio.run(links_module.link_message_handler(update_for(message), context))

    assert message.status.texts == ["Не знаю такой источник."]


def test_message_without_a_url_is_ignored(monkeypatch):
    allow_everyone(monkeypatch)
    message = FakeMessage("просто текст")

    class ExplodingLinks:
        async def fetch_info(self, url: str):
            raise AssertionError("must not fetch anything")

    context = context_for(links=ExplodingLinks(), db=FakeDb())
    asyncio.run(links_module.link_message_handler(update_for(message), context))

    assert message.replies == []


def test_cached_delivery_skips_download(monkeypatch):
    message = FakeMessage("")
    row = {
        "media_key": "abc123",
        "url_key": "youtube:abc123",
        "source": "youtube",
        "webpage_url": "https://youtu.be/abc123",
        "title": "Xtal",
        "artist": "Aphex Twin",
        "duration": 293,
        "thumbnail_url": None,
    }
    db = FakeDb(media=row, parts=[{"telegram_file_id": "f1"}])

    class FakeDelivery:
        def __init__(self) -> None:
            self.sent: list[list] = []

        async def send_cached(self, bot, *, chat_id: int, parts: list) -> bool:
            self.sent.append(parts)
            return True

        async def source_file(self, *args, **kwargs):
            raise AssertionError("must not download when parts are cached")

    delivery = FakeDelivery()
    context = context_for(db=db, external_delivery=delivery)
    asyncio.run(links_module.external_get_callback(update_for(message), context, "abc123"))

    assert delivery.sent == [[{"telegram_file_id": "f1"}]]
    assert db.touched == ["abc123"]
    assert message.status.deleted is True


def test_unknown_media_key_asks_for_the_link_again():
    message = FakeMessage("")
    context = context_for(db=FakeDb(media=None), external_delivery=None)

    asyncio.run(links_module.external_get_callback(update_for(message), context, "gone"))

    assert message.replies == [links_module.LINK_EXPIRED]
