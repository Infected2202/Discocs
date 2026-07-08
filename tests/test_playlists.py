"""Store tests for user playlists (plans/playlist.md, phase 1)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.scanner import ScannedTrack
from app.store import Store


def make_store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "app.db")
    store.init()
    return store


def add_track(store: Store, tmp_path: Path, name: str) -> int:
    path = tmp_path / f"{name}.flac"
    path.write_bytes(b"fake")
    stat = path.stat()
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=path,
            artist=f"Artist {name}",
            title=name,
            album="Album",
            duration=180.0,
            file_size=stat.st_size,
            mtime=int(stat.st_mtime),
        )
    )
    return track_id


def add_tracks(store: Store, tmp_path: Path, count: int) -> list[int]:
    return [add_track(store, tmp_path, f"track-{index}") for index in range(count)]


# ---------------------------------------------------------------------------
# create_playlist
# ---------------------------------------------------------------------------

def test_create_playlist_roundtrip(tmp_path: Path):
    store = make_store(tmp_path)
    tracks = add_tracks(store, tmp_path, 3)

    playlist = store.create_playlist(
        title="  Evening set  ",
        description="Slow burners",
        source={"visibility": "private"},
        track_ids=tracks,
    )

    assert playlist.title == "Evening set"
    assert playlist.kind == "manual"
    assert playlist.description == "Slow burners"
    assert playlist.cover_path is None
    assert '"visibility": "private"' in (playlist.source_json or "")
    assert store.playlist_track_ids(playlist.id) == tracks
    positions = [item.position for item in store.list_playlist_items(playlist.id)]
    assert positions == [0, 1, 2]


def test_create_playlist_dedups_input_track_ids(tmp_path: Path):
    store = make_store(tmp_path)
    first, second = add_tracks(store, tmp_path, 2)

    playlist = store.create_playlist(title="Dupes", track_ids=[first, second, first])

    assert store.playlist_track_ids(playlist.id) == [first, second]


def test_create_playlist_rejects_unknown_track(tmp_path: Path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError, match="Tracks not found"):
        store.create_playlist(title="Broken", track_ids=[9999])


def test_create_playlist_rejects_blank_title_and_bad_kind(tmp_path: Path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError, match="title"):
        store.create_playlist(title="   ")
    with pytest.raises(ValueError, match="kind"):
        store.create_playlist(title="X", kind="bogus")


# ---------------------------------------------------------------------------
# add / remove tracks
# ---------------------------------------------------------------------------

def test_add_playlist_tracks_appends_and_skips_duplicates(tmp_path: Path):
    store = make_store(tmp_path)
    tracks = add_tracks(store, tmp_path, 4)
    playlist = store.create_playlist(title="Grow", track_ids=tracks[:2])

    added = store.add_playlist_tracks(playlist.id, [tracks[2], tracks[0], tracks[3]])

    assert added == 2
    assert store.playlist_track_ids(playlist.id) == [tracks[0], tracks[1], tracks[2], tracks[3]]

    # Idempotent repeat: nothing added, updated_at untouched.
    before = store.get_playlist(playlist.id).updated_at
    assert store.add_playlist_tracks(playlist.id, [tracks[0]]) == 0
    assert store.get_playlist(playlist.id).updated_at == before


def test_add_playlist_tracks_missing_playlist(tmp_path: Path):
    store = make_store(tmp_path)
    track = add_track(store, tmp_path, "solo")
    with pytest.raises(ValueError, match="Playlist not found"):
        store.add_playlist_tracks(4242, [track])


def test_remove_playlist_tracks_repacks_positions(tmp_path: Path):
    store = make_store(tmp_path)
    tracks = add_tracks(store, tmp_path, 5)
    playlist = store.create_playlist(title="Trim", track_ids=tracks)

    removed = store.remove_playlist_tracks(playlist.id, [tracks[1], tracks[3], 12345])

    assert removed == 2
    items = store.list_playlist_items(playlist.id)
    assert [item.track_id for item in items] == [tracks[0], tracks[2], tracks[4]]
    assert [item.position for item in items] == [0, 1, 2]
    assert store.remove_playlist_tracks(playlist.id, []) == 0


# ---------------------------------------------------------------------------
# update / delete
# ---------------------------------------------------------------------------

def test_update_playlist_title_and_description(tmp_path: Path):
    store = make_store(tmp_path)
    playlist = store.create_playlist(title="Old", description="Keep me")

    renamed = store.update_playlist(playlist.id, title="New name")
    assert renamed.title == "New name"
    assert renamed.description == "Keep me"

    cleared = store.update_playlist(playlist.id, description=None)
    assert cleared.title == "New name"
    assert cleared.description is None

    assert store.update_playlist(999, title="Ghost") is None
    with pytest.raises(ValueError, match="title"):
        store.update_playlist(playlist.id, title="  ")


def test_delete_playlist_cascades_items(tmp_path: Path):
    store = make_store(tmp_path)
    tracks = add_tracks(store, tmp_path, 2)
    playlist = store.create_playlist(title="Doomed", track_ids=tracks)

    assert store.delete_playlist(playlist.id) is True
    assert store.get_playlist(playlist.id) is None
    assert store.list_playlist_items(playlist.id) == []
    assert store.delete_playlist(playlist.id) is False


def test_delete_saved_mix_playlist_reactivates_mix(tmp_path: Path):
    store = make_store(tmp_path)
    track = add_track(store, tmp_path, "seed")
    store.save_generated_mix(
        mix_id="mix-1",
        title="Test Mix",
        mix_type="taste_region",
        items=[{"track_id": track, "position": 0}],
    )
    saved = store.save_generated_mix_as_playlist("mix-1")
    assert saved.status == "saved"
    playlist_id = saved.saved_playlist_id

    assert store.delete_playlist(playlist_id) is True

    mix = store.get_generated_mix("mix-1")
    assert mix.status == "active"
    assert mix.saved_playlist_id is None


# ---------------------------------------------------------------------------
# listing / counts / cover
# ---------------------------------------------------------------------------

def test_list_playlists_orders_by_updated_at_desc(tmp_path: Path):
    store = make_store(tmp_path)
    track = add_track(store, tmp_path, "bump")
    first = store.create_playlist(title="First")
    second = store.create_playlist(title="Second")

    assert [p.id for p in store.list_playlists()] == [second.id, first.id]

    # Mutating the older playlist moves it to the top of "recents".
    store.add_playlist_tracks(first.id, [track])
    bumped = store.get_playlist(first.id)
    assert bumped.updated_at >= store.get_playlist(second.id).updated_at
    with store.connect() as conn:
        conn.execute(
            "UPDATE playlists SET updated_at = '2099-01-01T00:00:00+00:00' WHERE id = ?",
            (first.id,),
        )
    assert [p.id for p in store.list_playlists()] == [first.id, second.id]

    assert store.count_playlists() == 2
    listed = store.list_playlists(limit=1, offset=1)
    assert len(listed) == 1


def test_playlist_track_counts(tmp_path: Path):
    store = make_store(tmp_path)
    tracks = add_tracks(store, tmp_path, 3)
    full = store.create_playlist(title="Full", track_ids=tracks)
    empty = store.create_playlist(title="Empty")

    counts = store.playlist_track_counts([full.id, empty.id, 777])

    assert counts == {full.id: 3}
    assert store.playlist_track_counts([]) == {}


def test_set_playlist_cover_path_roundtrip(tmp_path: Path):
    store = make_store(tmp_path)
    playlist = store.create_playlist(title="Art")

    updated = store.set_playlist_cover_path(playlist.id, "data/playlist_covers/1.jpg")
    assert updated.cover_path == "data/playlist_covers/1.jpg"

    cleared = store.set_playlist_cover_path(playlist.id, None)
    assert cleared.cover_path is None
    assert store.set_playlist_cover_path(31337, "x.jpg") is None
