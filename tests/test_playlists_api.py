"""API tests for user playlists (plans/playlist.md, phase 3)."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import app.api.mixes as api_mixes_module
import app.api.playlists as api_playlists_module
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
    monkeypatch.delenv("DISCOCS_NAVIDROME_URL", raising=False)
    monkeypatch.delenv("DISCOCS_NAVIDROME_USER", raising=False)
    monkeypatch.delenv("DISCOCS_NAVIDROME_PASSWORD", raising=False)
    store = Store(db_path)
    store.init()
    return store


def add_track(store: Store, tmp_path: Path, name: str) -> int:
    path = tmp_path / "music" / f"{name}.flac"
    path.parent.mkdir(parents=True, exist_ok=True)
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


def test_playlist_crud_roundtrip(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    tracks = [add_track(store, tmp_path, f"t{i}") for i in range(2)]
    client = TestClient(app)

    created = client.post(
        "/api/v1/playlists",
        json={
            "title": "Evening set",
            "description": "Slow burners",
            "visibility": "private",
            "track_ids": tracks,
        },
    )
    assert created.status_code == 201
    playlist = created.json()
    assert playlist["title"] == "Evening set"
    assert playlist["kind"] == "manual"
    assert playlist["description"] == "Slow burners"
    assert playlist["track_count"] == 2
    assert playlist["source"] == {"visibility": "private"}
    assert playlist["action"]["target"] == f"/playlists/{playlist['id']}"

    listed = client.get("/api/v1/playlists")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == playlist["id"]
    assert listed.json()["next_offset"] is None

    detail = client.get(f"/api/v1/playlists/{playlist['id']}")
    assert detail.status_code == 200
    assert [t["id"] for t in detail.json()["tracks"]] == tracks

    patched = client.patch(
        f"/api/v1/playlists/{playlist['id']}",
        json={"title": "Night set", "description": None},
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "Night set"
    assert patched.json()["description"] is None

    deleted = client.delete(f"/api/v1/playlists/{playlist['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/playlists/{playlist['id']}").status_code == 404
    assert client.delete(f"/api/v1/playlists/{playlist['id']}").status_code == 404


def test_playlist_create_validation_errors(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    assert client.post("/api/v1/playlists", json={"title": ""}).status_code == 422
    assert client.post("/api/v1/playlists", json={"title": "   "}).status_code == 422
    assert (
        client.post("/api/v1/playlists", json={"title": "X", "track_ids": [999]}).status_code
        == 422
    )
    assert (
        client.post("/api/v1/playlists", json={"title": "X", "visibility": "friends"}).status_code
        == 422
    )


def test_playlist_add_and_remove_tracks(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    tracks = [add_track(store, tmp_path, f"t{i}") for i in range(4)]
    client = TestClient(app)
    playlist_id = client.post(
        "/api/v1/playlists", json={"title": "Grow", "track_ids": tracks[:2]}
    ).json()["id"]

    added = client.post(
        f"/api/v1/playlists/{playlist_id}/tracks",
        json={"track_ids": [tracks[2], tracks[0]]},
    )
    assert added.status_code == 200
    assert added.json() == {"added": 1, "track_count": 3}

    removed = client.post(
        f"/api/v1/playlists/{playlist_id}/tracks/remove",
        json={"track_ids": [tracks[0], tracks[3]]},
    )
    assert removed.status_code == 200
    assert removed.json() == {"removed": 1, "track_count": 2}

    detail = client.get(f"/api/v1/playlists/{playlist_id}")
    assert [t["id"] for t in detail.json()["tracks"]] == [tracks[1], tracks[2]]

    assert client.post("/api/v1/playlists/999/tracks", json={"track_ids": [tracks[0]]}).status_code == 404
    assert client.post("/api/v1/playlists/999/tracks/remove", json={"track_ids": [tracks[0]]}).status_code == 404
    assert (
        client.post(f"/api/v1/playlists/{playlist_id}/tracks", json={"track_ids": [4242]}).status_code
        == 422
    )
    assert client.post(f"/api/v1/playlists/{playlist_id}/tracks", json={"track_ids": []}).status_code == 422


def test_playlist_play_creates_session(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    tracks = [add_track(store, tmp_path, f"t{i}") for i in range(3)]
    client = TestClient(app)
    playlist_id = client.post(
        "/api/v1/playlists", json={"title": "Playable", "track_ids": tracks}
    ).json()["id"]

    played = client.post(f"/api/v1/playlists/{playlist_id}/play")

    assert played.status_code == 200
    envelope = played.json()
    assert envelope["session"]["source_type"] == "playlist"
    assert envelope["session"]["source_label"] == "Playable"
    assert [item["track_id"] for item in envelope["queue"]["items"]] == tracks

    empty_id = client.post("/api/v1/playlists", json={"title": "Empty"}).json()["id"]
    assert client.post(f"/api/v1/playlists/{empty_id}/play").status_code == 409
    assert client.post("/api/v1/playlists/999/play").status_code == 404


def test_playlist_cover_endpoint(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)
    playlist_id = client.post("/api/v1/playlists", json={"title": "Art"}).json()["id"]

    assert client.get(f"/api/v1/playlists/{playlist_id}/cover").status_code == 404
    assert client.get("/api/v1/playlists/999/cover").status_code == 404

    cover_file = tmp_path / "playlist_covers" / f"{playlist_id}.jpg"
    cover_file.parent.mkdir(parents=True, exist_ok=True)
    cover_file.write_bytes(b"fake-jpeg")
    store.set_playlist_cover_path(playlist_id, str(cover_file))

    response = client.get(f"/api/v1/playlists/{playlist_id}/cover")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == b"fake-jpeg"

    summary = client.get(f"/api/v1/playlists/{playlist_id}").json()
    assert summary["artwork"]["url"] == f"/api/v1/playlists/{playlist_id}/cover"


def test_mix_save_accepts_title_and_description(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    track_id = add_track(store, tmp_path, "seed")
    store.save_generated_mix(
        mix_id="mix-1",
        title="Mix Title",
        mix_type="taste_region",
        items=[{"track_id": track_id, "position": 0}],
    )
    cover_calls: list[int] = []
    monkeypatch.setattr(
        api_mixes_module,
        "refresh_playlist_cover",
        lambda _store, _settings, playlist_id: cover_calls.append(playlist_id),
    )
    client = TestClient(app)

    saved = client.post(
        "/api/v1/mixes/mix-1/save",
        json={"title": "My saved mix", "description": "From the mix page"},
    )

    assert saved.status_code == 200
    assert saved.json()["status"] == "saved"
    playlist_id = saved.json()["saved_playlist_id"]
    assert cover_calls == [playlist_id]

    playlist = client.get(f"/api/v1/playlists/{playlist_id}").json()
    assert playlist["title"] == "My saved mix"
    assert playlist["description"] == "From the mix page"
    assert playlist["kind"] == "saved_mix"
    assert [t["id"] for t in playlist["tracks"]] == [track_id]

    # Deleting the saved playlist makes the mix saveable again.
    assert client.delete(f"/api/v1/playlists/{playlist_id}").status_code == 204
    mix_detail = client.get("/api/v1/mixes/mix-1")
    assert mix_detail.status_code == 200
    assert mix_detail.json()["status"] == "active"
    assert mix_detail.json()["saved_playlist_id"] is None

    # Body-less save keeps working (backward compatible) and reuses mix title.
    resaved = client.post("/api/v1/mixes/mix-1/save")
    assert resaved.status_code == 200
    new_playlist_id = resaved.json()["saved_playlist_id"]
    assert client.get(f"/api/v1/playlists/{new_playlist_id}").json()["title"] == "Mix Title"


def test_likes_routes_still_reachable(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    # Navidrome is unconfigured — the point is that "likes" hits the likes
    # route (502) instead of the int-typed generic playlist route (422/404).
    assert client.get("/api/v1/playlists/likes").status_code == 502
    assert client.post("/api/v1/playlists/likes/play").status_code == 502
    assert api_playlists_module is not None
