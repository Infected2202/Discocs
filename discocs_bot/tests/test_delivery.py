import asyncio
from pathlib import Path
from types import SimpleNamespace

from bot.services.delivery import THUMBNAIL_COVER_SIZE, DeliveryService
from bot.storage.models import Track
from bot.storage.user_prefs import delivery_prefs_from_profile
from bot.utils.audio_quality import source_cache_profile


class _FakeDb:
    def __init__(self, entries: dict[str, str]) -> None:
        self._entries = entries

    async def get_cached_file_id(self, song_id: str, *, bitrate: str | None = None) -> str | None:
        return self._entries.get(bitrate)


def _service(entries: dict[str, str]) -> DeliveryService:
    return DeliveryService(settings=None, navidrome=None, transcoder=None, db=_FakeDb(entries))


def test_lookup_cached_ignores_stale_no_cover_entry_when_cover_now_available():
    # Regression test: before the fix, a track cached without a cover (e.g. during
    # a Navidrome outage) would be served from that stale cache forever, even after
    # cover art became available again, because Telegram ignores a new `thumbnail`
    # when the audio is resent by an existing file_id.
    prefs = delivery_prefs_from_profile("mp3:320")
    track = Track(id="song-1", title="T", artist="A", album="Alb", suffix="mp3")
    no_cover_key = prefs.cache_profile(with_cover=False)
    service = _service({no_cover_key: "old-file-id-without-cover"})

    result = asyncio.run(
        service._lookup_cached(track, prefs, source_bitrate_kbps=None, has_cover=True)
    )

    assert result is None


def test_lookup_cached_hits_entry_matching_current_cover_state():
    prefs = delivery_prefs_from_profile("mp3:320")
    track = Track(id="song-1", title="T", artist="A", album="Alb", suffix="mp3")
    with_cover_key = prefs.cache_profile(with_cover=True)
    service = _service({with_cover_key: "fresh-file-id"})

    result = asyncio.run(
        service._lookup_cached(track, prefs, source_bitrate_kbps=None, has_cover=True)
    )

    assert result is not None
    file_id, key, _as_document, _extension = result
    assert file_id == "fresh-file-id"
    assert key == with_cover_key


def test_lookup_cached_original_path_also_ignores_stale_no_cover_entry():
    # Same regression as above, but for the "send original file, no transcode"
    # branch: should_send_original() is always True for the flac profile, and
    # that key used to hardcode transcoded_with_cover=False regardless of the
    # track's actual cover state.
    prefs = delivery_prefs_from_profile("flac")
    track = Track(id="song-2", title="T", artist="A", album="Alb", suffix="flac")
    no_cover_key = source_cache_profile("flac", None, transcoded_with_cover=False)
    service = _service({no_cover_key: "old-file-id-without-cover"})

    result = asyncio.run(
        service._lookup_cached(track, prefs, source_bitrate_kbps=None, has_cover=True)
    )

    assert result is None


def test_prepare_audio_file_original_path_tags_cache_key_with_cover_state(tmp_path: Path):
    prefs = delivery_prefs_from_profile("flac")
    track = Track(id="song-2", title="T", artist="A", album="Alb", suffix="flac")
    service = _service({})
    cover_path = tmp_path / "cover.jpg"
    cover_path.write_bytes(b"fake-jpeg")

    _send_path, delivery = asyncio.run(
        service._prepare_audio_file(
            Path("source.flac"),
            Path("out.flac"),
            track,
            prefs=prefs,
            source_suffix="flac",
            source_bitrate_kbps=None,
            cover_path=cover_path,
        )
    )

    assert delivery.cache_bitrate == source_cache_profile(
        "flac", None, transcoded_with_cover=True
    )


class _FakeNavidromeCover:
    def __init__(self) -> None:
        self.cover_art_calls: list[tuple[str, int | None]] = []

    async def download_cover_art(self, cover_art_id: str, destination: Path, *, size: int | None = 600) -> bool:
        self.cover_art_calls.append((cover_art_id, size))
        destination.write_bytes(b"fake-jpeg")
        return True


def test_load_cover_requests_a_telegram_thumbnail_sized_cover(tmp_path: Path):
    # Telegram silently drops a `thumbnail` bigger than 320x320/200KB with no
    # error, so this must never regress back to Navidrome's oversized default.
    settings = SimpleNamespace(temp_dir=tmp_path)
    navidrome = _FakeNavidromeCover()
    service = DeliveryService(settings=settings, navidrome=navidrome, transcoder=None, db=_FakeDb({}))
    track = Track(id="song-3", title="T", artist="A", album="Alb", cover_art_id="cover-1")

    cover_path = asyncio.run(service._load_cover(track, "song-3"))

    assert cover_path is not None
    assert navidrome.cover_art_calls == [("cover-1", THUMBNAIL_COVER_SIZE)]


class _RecordingDb:
    def __init__(self) -> None:
        self.saved: list[dict[str, object]] = []

    async def save_cached_file_id(self, **payload) -> None:
        self.saved.append(payload)


def test_cache_from_messages_uses_audio_duration_and_document_payload():
    db = _RecordingDb()
    settings = SimpleNamespace(transcode_bitrate="320k")
    service = DeliveryService(settings=settings, navidrome=None, transcoder=None, db=db)
    prepared = [
        SimpleNamespace(
            track=Track(id="song-a", title="A", artist="Artist", album="Album"),
            cache_bitrate="mp3:320",
            as_document=False,
            duration=120,
        ),
        SimpleNamespace(
            track=Track(id="song-b", title="B", artist="Artist", album="Album"),
            cache_bitrate="flac",
            as_document=True,
            duration=240,
        ),
    ]
    messages = [
        SimpleNamespace(
            audio=SimpleNamespace(file_id="audio-id", file_unique_id="audio-uid", file_size=111, duration=123),
            document=None,
        ),
        SimpleNamespace(
            audio=None,
            document=SimpleNamespace(file_id="doc-id", file_unique_id="doc-uid", file_size=222),
        ),
    ]

    asyncio.run(service._cache_from_messages(messages, prepared))

    assert db.saved == [
        {
            "song_id": "song-a",
            "file_id": "audio-id",
            "file_unique_id": "audio-uid",
            "bitrate": "mp3:320",
            "file_size": 111,
            "duration": 123,
            "title": "A",
            "artist": "Artist",
            "album": "Album",
            "created_at": db.saved[0]["created_at"],
        },
        {
            "song_id": "song-b",
            "file_id": "doc-id",
            "file_unique_id": "doc-uid",
            "bitrate": "flac",
            "file_size": 222,
            "duration": 240,
            "title": "B",
            "artist": "Artist",
            "album": "Album",
            "created_at": db.saved[1]["created_at"],
        },
    ]
