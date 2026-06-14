from __future__ import annotations

from app.navidrome import NavidromeSong
from app.navidrome_sync import NAVIDROME_PROVIDER, _song_raw_json, sync_navidrome_catalog
from app.scanner import ScannedTrack
from app.store import Store


class FakeNavidromeClient:
    def __init__(self, songs: list[NavidromeSong]):
        self.songs = songs
        self.calls: list[tuple[int, int, int | None]] = []

    def iter_songs(self, *, page_size: int, query: str = "", limit: int | None = None):
        self.calls.append((page_size, len(query), limit))
        songs = self.songs if limit is None else self.songs[:limit]
        yield from songs


def song(item_id: str, title: str, size: int = 100) -> NavidromeSong:
    return NavidromeSong(
        id=item_id,
        title=title,
        artist="Artist",
        album="Album",
        duration=123,
        size=size,
        suffix="flac",
        content_type="audio/flac",
        genre="Techno",
        year=2001,
        raw={"id": item_id, "title": title, "size": size},
    )


def song_with_path(item_id: str, title: str, path: str, size: int = 100) -> NavidromeSong:
    return NavidromeSong(
        id=item_id,
        title=title,
        artist="Artist",
        album="Album",
        duration=123,
        size=size,
        suffix="flac",
        raw={"id": item_id, "title": title, "path": path, "size": size},
    )


def add_local_track(store: Store, path, title: str = "One") -> int:
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=path,
            artist="Artist",
            title=title,
            album="Album",
            duration=123.0,
            file_size=100,
            mtime=1,
        )
    )
    return track_id


def test_sync_navidrome_catalog_imports_all_songs(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    client = FakeNavidromeClient([song("song-1", "One"), song("song-2", "Two")])
    progress = []

    result = sync_navidrome_catalog(
        store,
        client,  # type: ignore[arg-type]
        page_size=50,
        progress=lambda count, item: progress.append((count, item.id)),
    )

    assert result.seen_count == 2
    assert result.imported_count == 2
    assert result.updated_count == 0
    assert result.failed_count == 0
    assert result.external_id_count == 2
    assert result.tracks_without_external_id == 0
    assert client.calls == [(50, 0, None)]
    assert progress == [(1, "song-1"), (2, "song-2")]
    first = store.get_track_by_external_id(NAVIDROME_PROVIDER, "song-1")
    assert first is not None
    assert first.path == "navidrome://song-1"
    assert first.title == "One"
    assert first.genre == "Techno"
    assert first.year == 2001
    assert store.external_id_for_track(NAVIDROME_PROVIDER, first.id) == "song-1"
    assert store.get_external_track(NAVIDROME_PROVIDER, "song-1").raw_json == (
        '{"id": "song-1", "size": 100, "title": "One"}'
    )


def test_sync_navidrome_catalog_is_idempotent_and_updates_metadata(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    first_result = sync_navidrome_catalog(
        store,
        FakeNavidromeClient([song("song-1", "One")]),  # type: ignore[arg-type]
    )
    track = store.get_track_by_external_id(NAVIDROME_PROVIDER, "song-1")

    second_result = sync_navidrome_catalog(
        store,
        FakeNavidromeClient([song("song-1", "One Updated", size=101)]),  # type: ignore[arg-type]
    )
    refreshed = store.get_track_by_external_id(NAVIDROME_PROVIDER, "song-1")

    assert first_result.imported_count == 1
    assert second_result.imported_count == 0
    assert second_result.updated_count == 1
    assert refreshed.id == track.id
    assert refreshed.title == "One Updated"
    assert refreshed.file_size == 101
    assert store.count_tracks() == 1
    assert store.count_external_tracks(NAVIDROME_PROVIDER) == 1


def test_sync_navidrome_catalog_skips_unchanged_existing_tracks(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    first_result = sync_navidrome_catalog(
        store,
        FakeNavidromeClient([song("song-1", "One")]),  # type: ignore[arg-type]
    )
    track = store.get_track_by_external_id(NAVIDROME_PROVIDER, "song-1")

    second_result = sync_navidrome_catalog(
        store,
        FakeNavidromeClient([song("song-1", "One")]),  # type: ignore[arg-type]
    )
    refreshed = store.get_track_by_external_id(NAVIDROME_PROVIDER, "song-1")

    assert first_result.imported_count == 1
    assert second_result.imported_count == 0
    assert second_result.updated_count == 0
    assert refreshed.id == track.id
    assert refreshed.title == "One"
    assert store.count_tracks() == 1
    assert store.count_external_tracks(NAVIDROME_PROVIDER) == 1


def test_sync_navidrome_catalog_preserves_migrated_external_mapping(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    local_path = tmp_path / "music" / "Artist" / "Album" / "01 - One.flac"
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(b"fake")
    local_id = add_local_track(store, local_path)
    store.upsert_external_track(NAVIDROME_PROVIDER, "song-1", local_id)

    result = sync_navidrome_catalog(
        store,
        FakeNavidromeClient([song_with_path("song-1", "One", "/container/music/Artist/Album/01 - One.flac")]),  # type: ignore[arg-type]
    )

    mapped = store.get_track_by_external_id(NAVIDROME_PROVIDER, "song-1")
    assert mapped is not None
    assert mapped.id == local_id
    assert not mapped.path.startswith("navidrome://")
    assert result.imported_count == 0
    assert result.updated_count == 1
    assert store.count_tracks() == 1


def test_sync_navidrome_catalog_skips_unchanged_migrated_mapping(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    local_path = tmp_path / "music" / "Artist" / "Album" / "01 - One.flac"
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(b"fake")
    local_id = add_local_track(store, local_path)
    item = song_with_path("song-1", "One", "/container/music/Artist/Album/01 - One.flac")
    store.upsert_external_track(NAVIDROME_PROVIDER, "song-1", local_id, raw_json=_song_raw_json(item))

    result = sync_navidrome_catalog(store, FakeNavidromeClient([item]))  # type: ignore[arg-type]

    mapped = store.get_track_by_external_id(NAVIDROME_PROVIDER, "song-1")
    assert mapped is not None
    assert mapped.id == local_id
    assert result.imported_count == 0
    assert result.updated_count == 0
    assert store.count_tracks() == 1


def test_sync_navidrome_catalog_does_not_repair_unmigrated_local_tracks(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    local_path = tmp_path / "music" / "Artist" / "Album" / "01 - One.flac"
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(b"fake")
    add_local_track(store, local_path)

    result = sync_navidrome_catalog(
        store,
        FakeNavidromeClient([song_with_path("song-1", "One", "/music/Artist/Album/01 - One.flac")]),  # type: ignore[arg-type]
    )

    mapped = store.get_track_by_external_id(NAVIDROME_PROVIDER, "song-1")
    assert mapped is not None
    assert mapped.path == "navidrome://song-1"
    assert result.imported_count == 1
    assert result.updated_count == 0
    assert store.count_tracks() == 2


def test_sync_navidrome_catalog_marks_absent_tracks_stale(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    sync_navidrome_catalog(
        store,
        FakeNavidromeClient([song("song-1", "One"), song("song-2", "Two")]),  # type: ignore[arg-type]
    )

    result = sync_navidrome_catalog(
        store,
        FakeNavidromeClient([song("song-1", "One")]),  # type: ignore[arg-type]
        mark_stale=True,
    )

    assert result.stale_count == 1
    stale = store.get_track_by_external_id(NAVIDROME_PROVIDER, "song-2")
    assert stale is not None
    assert stale.missing_at is not None


def test_limited_sync_does_not_mark_unseen_tracks_stale(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    sync_navidrome_catalog(
        store,
        FakeNavidromeClient([song("song-1", "One"), song("song-2", "Two")]),  # type: ignore[arg-type]
    )

    result = sync_navidrome_catalog(
        store,
        FakeNavidromeClient([song("song-1", "One"), song("song-2", "Two")]),  # type: ignore[arg-type]
        limit=1,
        mark_stale=True,
    )

    assert result.seen_count == 1
    assert result.stale_count == 0
    assert store.get_track_by_external_id(NAVIDROME_PROVIDER, "song-2").missing_at is None
