import asyncio

from bot.services.delivery import DeliveryService
from bot.storage.models import Track
from bot.storage.user_prefs import delivery_prefs_from_profile


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
