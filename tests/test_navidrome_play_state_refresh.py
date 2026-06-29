from __future__ import annotations

from app.navidrome_sync import (
    NAVIDROME_PROVIDER,
    refresh_navidrome_play_state,
    sync_navidrome_catalog,
)
from app.store import Store

from tests.test_navidrome_sync import FakeNavidromeClient, song


class FakeRecentClient:
    """Client exposing only the recent-plays delta endpoint."""

    def __init__(self, songs):
        self.songs = songs
        self.calls: list[int] = []

    def iter_recent_played_songs(self, *, album_count: int = 25):
        self.calls.append(album_count)
        yield from self.songs


def _seed_catalog(store: Store) -> None:
    sync_navidrome_catalog(
        store,
        FakeNavidromeClient(
            [song("song-1", "One", play_count=1, played="2026-06-01T00:00:00Z")]
        ),  # type: ignore[arg-type]
    )


def test_refresh_updates_play_state_for_known_track(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    _seed_catalog(store)
    track = store.get_track_by_external_id(NAVIDROME_PROVIDER, "song-1")
    assert track is not None

    client = FakeRecentClient(
        [song("song-1", "One", play_count=7, played="2026-06-30T12:00:00Z")]
    )
    result = refresh_navidrome_play_state(store, client, album_count=10)  # type: ignore[arg-type]

    assert client.calls == [10]
    assert result.seen_count == 1
    assert result.updated_count == 1
    assert result.unmapped_count == 0

    pref = store.get_track_preference(track.id)
    assert pref is not None
    assert pref.play_count == 7
    assert pref.last_played_at == "2026-06-30T12:00:00Z"


def test_refresh_does_not_lower_play_count(tmp_path):
    """import_external_track_play_state only ratchets upward — refresh must not regress."""
    store = Store(tmp_path / "app.db")
    store.init()
    _seed_catalog(store)
    track = store.get_track_by_external_id(NAVIDROME_PROVIDER, "song-1")
    assert track is not None
    # Bump locally to a high value first.
    store.import_external_track_play_state(track.id, play_count=10)

    client = FakeRecentClient([song("song-1", "One", play_count=3)])
    refresh_navidrome_play_state(store, client)  # type: ignore[arg-type]

    pref = store.get_track_preference(track.id)
    assert pref is not None
    assert pref.play_count == 10


def test_refresh_skips_unmapped_songs(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    _seed_catalog(store)

    client = FakeRecentClient(
        [
            song("song-1", "One", play_count=5),
            song("ghost", "Unknown", play_count=2),
        ]
    )
    result = refresh_navidrome_play_state(store, client)  # type: ignore[arg-type]

    assert result.seen_count == 2
    assert result.updated_count == 1
    assert result.unmapped_count == 1
