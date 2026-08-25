"""The chat flow for external seeds: link cards, audio cards, delivery, radio."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.pop("bot", None)

from bot.handlers import links as links_module
from bot.services.discocs import DiscocsError
from bot.services.external_audio import ExternalAudioError, ExternalTrackInfo
from bot.storage.models import Track
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


def media_row(source: str = "youtube", telegram_file_id: str | None = None) -> dict:
    return {
        "media_key": "abc123",
        "url_key": f"{source}:abc123",
        "source": source,
        "webpage_url": "https://youtu.be/abc123",
        "telegram_file_id": telegram_file_id,
        "title": "Xtal",
        "artist": "Aphex Twin",
        "duration": 293,
        "thumbnail_url": None,
    }


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
    def __init__(self, text: str = "", audio=None) -> None:
        self.text = text
        self.caption = None
        self.audio = audio
        self.chat_id = 42
        self.status = FakeStatus()
        self.photos: list[dict] = []
        self.replies: list[tuple[str, object]] = []

    async def reply_text(self, text: str, **kwargs) -> FakeStatus:
        self.replies.append((text, kwargs.get("reply_markup")))
        return self.status

    async def reply_photo(self, **kwargs) -> None:
        self.photos.append(kwargs)

    def get_bot(self):
        return SimpleNamespace(name="bot")


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


class FakeNavidrome:
    def __init__(self, results: list[Track] | None = None) -> None:
        self.results = results or []
        self.queries: list[str] = []

    async def search_tracks(self, query: str, limit: int = 10, offset: int = 0):
        self.queries.append(query)
        return self.results, False


def update_for(message: FakeMessage) -> SimpleNamespace:
    return SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=7),
        callback_query=None,
    )


def context_for(tmp_path: Path | None = None, **bot_data) -> SimpleNamespace:
    data = {
        "settings": SimpleNamespace(temp_dir=(tmp_path or Path("temp")) / "tmp"),
        "navidrome": FakeNavidrome(),
    }
    data.update(bot_data)
    # user_data is where PTB keeps the carousel view and the last-track memory.
    return SimpleNamespace(bot=SimpleNamespace(name="bot"), bot_data=data, user_data={})


def allow_everyone(monkeypatch) -> None:
    async def allowed(_update, _context) -> bool:
        return False

    monkeypatch.setattr(links_module, "deny_if_not_allowed", allowed)


# ---------------------------------------------------------------------------
# Captions
# ---------------------------------------------------------------------------

def test_card_caption_shows_source_artist_and_duration():
    assert links_module.card_caption(info()) == "🔗 youtube\nAphex Twin — Xtal\n4:53"


def test_format_duration_handles_hours():
    assert links_module.format_duration(7325) == "2:02:05"
    assert links_module.format_duration(None) == ""


def test_audio_caption_explains_why_radio_is_missing():
    caption = links_module.audio_caption(info(), can_analyze=False)

    assert "20 МБ" in caption
    assert "Aphex Twin — Xtal" in caption


# ---------------------------------------------------------------------------
# Link cards
# ---------------------------------------------------------------------------

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
    keyboard = message.photos[0]["reply_markup"].inline_keyboard
    assert [button.callback_data for button in keyboard[0]] == ["extget:abc123", "extradio:abc123"]
    assert db.saved[0]["url_key"] == "youtube:abc123"
    assert db.events == ["external_link"]


def test_track_already_in_the_library_is_offered_instead_of_a_download(monkeypatch, tmp_path: Path):
    allow_everyone(monkeypatch)
    monkeypatch.setattr(links_module, "validate_public_url", lambda url: url)
    sent: list[dict] = []

    async def fake_send_track_card(bot, chat_id, track, **kwargs):
        sent.append({"track": track, **kwargs})

    monkeypatch.setattr(links_module, "send_track_card", fake_send_track_card)
    library_track = Track(id="song-9", title="Xtal", artist="Aphex Twin", album="SAW 85-92")
    message = FakeMessage("https://youtu.be/abc123")

    class FakeLinks:
        async def fetch_info(self, url: str) -> ExternalTrackInfo:
            return info()

    context = context_for(
        tmp_path,
        links=FakeLinks(),
        db=FakeDb(),
        navidrome=FakeNavidrome([library_track]),
    )
    asyncio.run(links_module.link_message_handler(update_for(message), context))

    assert message.photos == []
    assert len(sent) == 1
    assert sent[0]["track"].id == "song-9"
    buttons = [
        button.callback_data
        for row in sent[0]["keyboard"].inline_keyboard
        for button in row
    ]
    assert "get:song-9" in buttons
    assert "radio:song-9" in buttons
    # A wrong match must not trap anyone: the link download stays reachable.
    assert "extget:abc123" in buttons


def test_a_different_song_in_the_library_does_not_hijack_the_link(monkeypatch, tmp_path: Path):
    allow_everyone(monkeypatch)
    monkeypatch.setattr(links_module, "validate_public_url", lambda url: url)
    monkeypatch.setattr(
        links_module,
        "send_track_card",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not send a library card")),
    )
    other = Track(id="song-9", title="Ageispolis", artist="Aphex Twin", album="SAW 85-92")
    message = FakeMessage("https://youtu.be/abc123")

    class FakeLinks:
        async def fetch_info(self, url: str) -> ExternalTrackInfo:
            return info()

    context = context_for(tmp_path, links=FakeLinks(), db=FakeDb(), navidrome=FakeNavidrome([other]))
    asyncio.run(links_module.link_message_handler(update_for(message), context))

    assert len(message.photos) == 1


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


# ---------------------------------------------------------------------------
# Audio files
# ---------------------------------------------------------------------------

def audio(file_size: int = 5 * 1024 * 1024, **overrides) -> SimpleNamespace:
    base = {
        "file_id": "file-1",
        "file_unique_id": "uniq-1",
        "performer": "Aphex Twin",
        "title": "Xtal",
        "file_name": "aphex.mp3",
        "duration": 293,
        "file_size": file_size,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_audio_message_offers_search_and_radio(monkeypatch):
    allow_everyone(monkeypatch)
    message = FakeMessage(audio=audio())
    db = FakeDb()
    context = context_for(db=db)

    asyncio.run(links_module.audio_message_handler(update_for(message), context))

    text, markup = message.replies[0]
    assert "Aphex Twin — Xtal" in text
    buttons = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert buttons[0].startswith("extsearch:")
    assert any(data.startswith("extradio:") for data in buttons)
    assert db.saved[0]["telegram_file_id"] == "file-1"
    assert db.saved[0]["source"] == "telegram"


def test_audio_too_large_for_telegram_offers_search_only(monkeypatch):
    allow_everyone(monkeypatch)
    message = FakeMessage(audio=audio(file_size=40 * 1024 * 1024))
    context = context_for(db=FakeDb())

    asyncio.run(links_module.audio_message_handler(update_for(message), context))

    text, markup = message.replies[0]
    buttons = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert not any(data.startswith("extradio:") for data in buttons)
    assert "20 МБ" in text


def test_audio_without_tags_falls_back_to_the_file_name(monkeypatch):
    allow_everyone(monkeypatch)
    message = FakeMessage(audio=audio(performer=None, title=None, file_name="Boards of Canada - Roygbiv.mp3"))
    db = FakeDb()

    asyncio.run(links_module.audio_message_handler(update_for(message), context_for(db=db)))

    assert db.saved[0]["artist"] == "Boards of Canada"
    assert db.saved[0]["title"].startswith("Roygbiv")


def test_tag_search_button_searches_the_stored_metadata(monkeypatch):
    message = FakeMessage()
    db = FakeDb(media=media_row(source="telegram", telegram_file_id="file-1"))
    queries: list[str] = []

    async def fake_run_search(update, context, query):
        queries.append(query)

    from bot.handlers import search as search_module

    monkeypatch.setattr(search_module, "run_search", fake_run_search)
    context = context_for(db=db)

    asyncio.run(links_module.external_search_callback(update_for(message), context, "abc123"))

    assert queries == ["Aphex Twin Xtal"]


# ---------------------------------------------------------------------------
# Delivery and radio
# ---------------------------------------------------------------------------

def test_cached_delivery_skips_download():
    message = FakeMessage()
    db = FakeDb(media=media_row(), parts=[{"telegram_file_id": "f1"}])

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
    message = FakeMessage()
    context = context_for(db=FakeDb(media=None), external_delivery=None)

    asyncio.run(links_module.external_get_callback(update_for(message), context, "gone"))

    assert message.replies[0][0] == links_module.LINK_EXPIRED


class FakeDiscocs:
    def __init__(self, tracks: list[Track] | None = None, error: Exception | None = None) -> None:
        self.tracks = tracks or []
        self.error = error
        self.analyzed: list[Path] = []

    async def get_similar_by_audio(self, path: Path, **kwargs):
        if self.error:
            raise self.error
        self.analyzed.append(path)
        return self.tracks


def test_radio_from_a_link_analyzes_the_download_and_renders_a_page(monkeypatch, tmp_path: Path):
    message = FakeMessage()
    source = tmp_path / "abc123.opus"
    source.write_bytes(b"audio")
    pages: list[dict] = []

    async def fake_send_page(msg, tracks, **kwargs):
        pages.append({"tracks": tracks, **kwargs})

    monkeypatch.setattr(links_module, "send_track_results_page", fake_send_page)

    class FakeDelivery:
        async def source_file(self, url: str, media_info):
            assert url == "https://youtu.be/abc123"
            return source

        async def telegram_source_file(self, *args, **kwargs):
            raise AssertionError("a link must not be fetched from Telegram")

    discocs = FakeDiscocs([Track(id="s1", title="Near", artist="A", album="B")])
    db = FakeDb(media=media_row())
    context = context_for(tmp_path, db=db, discocs=discocs, external_delivery=FakeDelivery())

    asyncio.run(links_module.external_radio_callback(update_for(message), context, "abc123"))

    assert discocs.analyzed == [source]
    assert len(pages) == 1
    assert pages[0]["page_kind"] == "external_radio"
    assert pages[0]["session_key"] == "abc123"
    # Another page would mean uploading and embedding the same audio again.
    assert pages[0]["has_next"] is False
    assert pages[0]["header"] == "Радио от Aphex Twin — Xtal"
    assert db.events == ["external_radio"]


def test_radio_from_a_sent_file_uses_the_telegram_download(monkeypatch, tmp_path: Path):
    message = FakeMessage()
    source = tmp_path / "abc123.mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(links_module, "send_track_results_page", _noop_page)

    class FakeDelivery:
        def __init__(self) -> None:
            self.file_ids: list[str] = []

        async def telegram_source_file(self, bot, file_id: str, media_info):
            self.file_ids.append(file_id)
            return source

        async def source_file(self, *args, **kwargs):
            raise AssertionError("a sent file must not go through yt-dlp")

    delivery = FakeDelivery()
    discocs = FakeDiscocs([Track(id="s1", title="Near", artist="A", album="B")])
    context = context_for(
        tmp_path,
        db=FakeDb(media=media_row(source="telegram", telegram_file_id="file-1")),
        discocs=discocs,
        external_delivery=delivery,
    )

    asyncio.run(links_module.external_radio_callback(update_for(message), context, "abc123"))

    assert delivery.file_ids == ["file-1"]
    assert discocs.analyzed == [source]


def test_radio_reports_an_empty_result(monkeypatch, tmp_path: Path):
    message = FakeMessage()
    source = tmp_path / "abc123.mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(links_module, "send_track_results_page", _noop_page)

    class FakeDelivery:
        async def source_file(self, url: str, media_info):
            return source

    context = context_for(
        tmp_path,
        db=FakeDb(media=media_row()),
        discocs=FakeDiscocs([]),
        external_delivery=FakeDelivery(),
    )

    asyncio.run(links_module.external_radio_callback(update_for(message), context, "abc123"))

    assert message.status.texts[-1] == links_module.NO_RADIO


def test_radio_reports_a_backend_failure(monkeypatch, tmp_path: Path):
    message = FakeMessage()
    source = tmp_path / "abc123.mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(links_module, "send_track_results_page", _noop_page)

    class FakeDelivery:
        async def source_file(self, url: str, media_info):
            return source

    context = context_for(
        tmp_path,
        db=FakeDb(media=media_row()),
        discocs=FakeDiscocs(error=DiscocsError("boom", user_message="Discocs сейчас недоступен.")),
        external_delivery=FakeDelivery(),
    )

    asyncio.run(links_module.external_radio_callback(update_for(message), context, "abc123"))

    assert message.status.texts[-1] == "Discocs сейчас недоступен."


async def _noop_page(*args, **kwargs) -> None:
    return None
