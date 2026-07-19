"""Phase 3 of the likes unification: every star endpoint mirrors locally.

Before this, starring an album told Navidrome but wrote nothing local, so a real
album like could never reach the favourites shelf, while the shelf filled up
with albums inferred from track stars. See plans/likes-unification-plan.md.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.api.deps as api_deps_module
from app.main import app
from app.scanner import ScannedTrack
from app.store import INITIALIZED_DB_PATHS, Store


def init_api_store(tmp_path: Path, monkeypatch) -> Store:
    db_path = tmp_path / "app.db"
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    monkeypatch.setenv("DISCOCS_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("DISCOCS_NAVIDROME_URL", "http://navidrome:4533")
    monkeypatch.setenv("DISCOCS_NAVIDROME_USER", "alice")
    monkeypatch.setenv("DISCOCS_NAVIDROME_PASSWORD", "secret")
    monkeypatch.setenv("DISCOCS_NAVIDROME_AUTH_MODE", "plain")
    store = Store(db_path)
    store.init()
    return store


def _add_track(store: Store, tmp_path: Path, item_id: str) -> tuple[int, int, int]:
    """Add one Navidrome-mapped track; return (track_id, release_id, artist_id)."""
    track_id, _ = store.upsert_track(
        ScannedTrack(
            path=(tmp_path / f"{item_id}.flac").resolve(),
            artist="Some Artist",
            title="Some Track",
            album="Some Album",
            duration=120.0,
            file_size=1,
            mtime=1,
        )
    )
    store.upsert_external_track("navidrome", item_id, track_id)
    artist_id = store.artist_ids_for_track(track_id)[0]
    with store.connect() as conn:
        release_id = int(
            conn.execute(
                "SELECT release_id FROM release_tracks WHERE track_id = ?", (track_id,)
            ).fetchone()["release_id"]
        )
    return track_id, release_id, artist_id


def _map_external(store: Store, entity_type: str, external_id: str, entity_id: int) -> None:
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO external_ids (provider, entity_type, entity_id, external_id, synced_at) "
            "VALUES ('navidrome', ?, ?, ?, '2026-01-01T00:00:00+00:00')",
            (entity_type, entity_id, external_id),
        )


def _release_liked(store: Store, release_id: int) -> bool:
    pref = store.get_release_preference(release_id)
    return bool(pref and pref.liked)


def _artist_liked(store: Store, artist_id: int) -> bool:
    pref = store.get_artist_preference(artist_id)
    return bool(pref and pref.liked)


class FakeStarClient:
    """Accepts every star call and records nothing but the fact it happened."""

    calls: list[tuple[str, str]] = []

    def __init__(self, settings):
        self.settings = settings

    def _record(self, action: str, item_id: str) -> None:
        FakeStarClient.calls.append((action, item_id))

    def star_song(self, item_id: str) -> None:
        self._record("star_song", item_id)

    def unstar_song(self, item_id: str) -> None:
        self._record("unstar_song", item_id)

    def star_album(self, item_id: str) -> None:
        self._record("star_album", item_id)

    def unstar_album(self, item_id: str) -> None:
        self._record("unstar_album", item_id)

    def star_artist(self, item_id: str) -> None:
        self._record("star_artist", item_id)

    def unstar_artist(self, item_id: str) -> None:
        self._record("unstar_artist", item_id)


@pytest.fixture
def star_client(monkeypatch):
    FakeStarClient.calls = []
    monkeypatch.setattr(api_deps_module, "NavidromeClient", FakeStarClient)
    return FakeStarClient


def test_album_star_is_mirrored_into_local_likes(tmp_path: Path, monkeypatch, star_client):
    store = init_api_store(tmp_path, monkeypatch)
    _track_id, release_id, _artist_id = _add_track(store, tmp_path, "song-1")
    _map_external(store, "release", "album-1", release_id)
    client = TestClient(app)

    response = client.put(
        f"/api/v1/releases/{release_id}/navidrome-star", json={"starred": True}
    )

    assert response.status_code == 200
    assert _release_liked(store, release_id) is True


def test_album_unstar_clears_the_local_like(tmp_path: Path, monkeypatch, star_client):
    store = init_api_store(tmp_path, monkeypatch)
    _track_id, release_id, _artist_id = _add_track(store, tmp_path, "song-1")
    _map_external(store, "release", "album-1", release_id)
    client = TestClient(app)

    client.put(f"/api/v1/releases/{release_id}/navidrome-star", json={"starred": True})
    client.put(f"/api/v1/releases/{release_id}/navidrome-star", json={"starred": False})

    assert _release_liked(store, release_id) is False


def test_artist_star_is_mirrored_into_local_likes(tmp_path: Path, monkeypatch, star_client):
    store = init_api_store(tmp_path, monkeypatch)
    _track_id, _release_id, artist_id = _add_track(store, tmp_path, "song-1")
    _map_external(store, "artist", "artist-1", artist_id)
    client = TestClient(app)

    response = client.put(
        f"/api/v1/artists/{artist_id}/navidrome-star", json={"starred": True}
    )

    assert response.status_code == 200
    assert _artist_liked(store, artist_id) is True


def test_track_star_does_not_like_its_release_or_artist(tmp_path: Path, monkeypatch, star_client):
    """The original bug, at the API level."""
    store = init_api_store(tmp_path, monkeypatch)
    track_id, release_id, artist_id = _add_track(store, tmp_path, "song-1")
    client = TestClient(app)

    response = client.put(
        f"/api/v1/tracks/{track_id}/navidrome-star", json={"starred": True}
    )

    assert response.status_code == 200
    assert store.get_track_preference(track_id).liked is True
    assert store.get_release_preference(release_id) is None
    assert store.get_artist_preference(artist_id) is None


def test_track_unstar_clears_the_local_like(tmp_path: Path, monkeypatch, star_client):
    store = init_api_store(tmp_path, monkeypatch)
    track_id, _release_id, _artist_id = _add_track(store, tmp_path, "song-1")
    client = TestClient(app)

    client.put(f"/api/v1/tracks/{track_id}/navidrome-star", json={"starred": True})
    client.put(f"/api/v1/tracks/{track_id}/navidrome-star", json={"starred": False})

    assert store.get_track_preference(track_id).liked is False


def test_starred_ids_syncs_releases_too(tmp_path: Path, monkeypatch):
    """Album likes had no sync at all before — stale entries could never clear."""
    store = init_api_store(tmp_path, monkeypatch)
    _track_id, release_id, _artist_id = _add_track(store, tmp_path, "song-1")
    _map_external(store, "release", "album-1", release_id)
    store.set_release_liked(release_id, True)

    class FakeNavidromeClient:
        def __init__(self, settings):
            self.settings = settings

        def get_starred_full(self):
            # Navidrome no longer reports this album as starred.
            return {"songs": [], "albums": [], "artists": []}

    monkeypatch.setattr(api_deps_module, "NavidromeClient", FakeNavidromeClient)
    client = TestClient(app)

    response = client.get("/api/v1/navidrome/starred/ids")

    assert response.status_code == 200
    assert _release_liked(store, release_id) is False


def test_starred_ids_restores_likes_from_navidrome(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    _track_id, release_id, artist_id = _add_track(store, tmp_path, "song-1")
    _map_external(store, "release", "album-1", release_id)
    _map_external(store, "artist", "artist-1", artist_id)

    class FakeNavidromeClient:
        def __init__(self, settings):
            self.settings = settings

        def get_starred_full(self):
            return {
                "songs": [],
                "albums": [{"id": "album-1"}],
                "artists": [{"id": "artist-1"}],
            }

    monkeypatch.setattr(api_deps_module, "NavidromeClient", FakeNavidromeClient)
    client = TestClient(app)

    response = client.get("/api/v1/navidrome/starred/ids")

    assert response.status_code == 200
    assert response.json()["album_ids"] == [release_id]
    assert response.json()["artist_ids"] == [artist_id]
    assert _release_liked(store, release_id) is True
    assert _artist_liked(store, artist_id) is True


def test_likes_playlist_does_not_create_entity_likes(tmp_path: Path, monkeypatch):
    """Opening the liked-tracks playlist used to seed artist likes on every visit."""
    store = init_api_store(tmp_path, monkeypatch)
    track_id, release_id, artist_id = _add_track(store, tmp_path, "song-1")

    class FakeNavidromeClient:
        def __init__(self, settings):
            self.settings = settings

        def get_starred_full(self):
            return {
                "songs": [{"id": "song-1", "title": "Some Track", "artist": "Some Artist"}],
                "albums": [],
                "artists": [],
            }

    monkeypatch.setattr(api_deps_module, "NavidromeClient", FakeNavidromeClient)
    client = TestClient(app)

    response = client.get("/api/v1/playlists/likes")

    assert response.status_code == 200
    assert store.get_track_preference(track_id).liked is True
    assert _release_liked(store, release_id) is False
    assert _artist_liked(store, artist_id) is False
