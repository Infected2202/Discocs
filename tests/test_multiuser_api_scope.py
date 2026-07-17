"""API-boundary isolation for request-local user scoped stores."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import auth
from app.main import app
from app.store import INITIALIZED_DB_PATHS, Store


def _init_store(tmp_path: Path, monkeypatch) -> Store:
    db_path = tmp_path / "app.db"
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    monkeypatch.setenv("DISCOCS_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("DISCOCS_AUTH_ENABLED", "true")
    monkeypatch.setenv("DISCOCS_NAVIDROME_URL", "http://navidrome:4533")
    store = Store(db_path)
    store.init()
    return store


def _login(username: str) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "correct"},
    )
    assert response.status_code == 200
    return client


def test_two_sessions_cannot_cross_api_personal_boundaries(tmp_path, monkeypatch):
    """The middleware identity must scope every downstream store operation."""
    _init_store(tmp_path, monkeypatch)
    monkeypatch.setattr(
        auth,
        "verify_navidrome_credentials",
        lambda _settings, username, password, **_kwargs: (
            username in {"alice", "bob"} and password == "correct"
        ),
    )
    monkeypatch.setattr(
        auth, "sync_navidrome_starred_for_user", lambda *_args, **_kwargs: None
    )

    alice = _login("alice")
    bob = _login("bob")

    alice_playlist = alice.post(
        "/api/v1/playlists",
        json={"title": "Alice private", "track_ids": []},
    )
    assert alice_playlist.status_code == 201
    alice_playlist_id = alice_playlist.json()["id"]

    alice_session = alice.post(
        "/api/v1/playback/sessions",
        json={"source_type": "manual", "source_label": "Alice queue"},
    )
    assert alice_session.status_code == 200
    alice_session_id = alice_session.json()["session"]["id"]

    assert alice.get("/api/v1/playlists").json()["total"] == 1
    assert bob.get("/api/v1/playlists").json()["total"] == 0
    assert bob.get(f"/api/v1/playlists/{alice_playlist_id}").status_code == 404
    assert bob.get(f"/api/v1/playback/sessions/{alice_session_id}").status_code == 404

    bob_playlist = bob.post(
        "/api/v1/playlists",
        json={"title": "Bob private", "track_ids": []},
    )
    assert bob_playlist.status_code == 201
    bob_playlist_id = bob_playlist.json()["id"]

    assert bob.get("/api/v1/playlists").json()["total"] == 1
    assert alice.get("/api/v1/playlists").json()["total"] == 1
    assert alice.get(f"/api/v1/playlists/{bob_playlist_id}").status_code == 404

    public_playlist = alice.post(
        "/api/v1/playlists",
        json={"title": "Alice public", "visibility": "public", "track_ids": []},
    )
    assert public_playlist.status_code == 201
    public_id = public_playlist.json()["id"]
    assert public_playlist.json()["editable"] is True
    assert public_playlist.json()["visibility"] == "public"

    bob_public = bob.get(f"/api/v1/playlists/{public_id}")
    assert bob_public.status_code == 200
    assert bob_public.json()["editable"] is False
    assert bob_public.json()["visibility"] == "public"
    assert bob.get("/api/v1/playlists").json()["total"] == 2
    assert bob.patch(
        f"/api/v1/playlists/{public_id}", json={"title": "Hijacked"}
    ).status_code == 404
    assert bob.delete(f"/api/v1/playlists/{public_id}").status_code == 404
    assert alice.get(f"/api/v1/playlists/{public_id}").json()["title"] == "Alice public"


def test_interactive_navidrome_client_uses_each_session_credentials(tmp_path, monkeypatch):
    _init_store(tmp_path, monkeypatch)
    monkeypatch.setattr(
        auth,
        "verify_navidrome_credentials",
        lambda _settings, username, password, **_kwargs: password == f"{username}-password",
    )
    monkeypatch.setattr(
        auth, "sync_navidrome_starred_for_user", lambda *_args, **_kwargs: None
    )
    captured: list[tuple[str, str]] = []

    class FakeNavidromeClient:
        def __init__(self, settings):
            captured.append((settings.user, settings.password))

        def get_starred_full(self):
            return {"songs": [], "albums": [], "artists": []}

    monkeypatch.setattr("app.api.deps.NavidromeClient", FakeNavidromeClient)

    alice = TestClient(app)
    bob = TestClient(app)
    assert alice.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "alice-password"},
    ).status_code == 200
    assert bob.post(
        "/api/v1/auth/login",
        json={"username": "bob", "password": "bob-password"},
    ).status_code == 200

    assert alice.get("/api/v1/navidrome/starred/ids").status_code == 200
    assert bob.get("/api/v1/navidrome/starred/ids").status_code == 200
    assert captured == [
        ("alice", "alice-password"),
        ("bob", "bob-password"),
    ]


def test_play_state_refresh_uses_active_user_credentials_and_store(tmp_path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    monkeypatch.setattr(
        auth,
        "verify_navidrome_credentials",
        lambda _settings, username, password, **_kwargs: password == f"{username}-password",
    )
    monkeypatch.setattr(
        auth, "sync_navidrome_starred_for_user", lambda *_args, **_kwargs: None
    )
    calls: list[tuple[int, str, str, int]] = []

    class FakeNavidromeClient:
        def __init__(self, settings):
            self.settings = settings

    def fake_refresh(scoped_store, client, *, album_count):
        calls.append(
            (
                scoped_store.user_id,
                client.settings.user,
                client.settings.password,
                album_count,
            )
        )
        return SimpleNamespace(seen_count=4, updated_count=3, unmapped_count=1)

    monkeypatch.setattr("app.api.deps.NavidromeClient", FakeNavidromeClient)
    monkeypatch.setattr("app.navidrome_sync.refresh_navidrome_play_state", fake_refresh)

    alice = TestClient(app)
    bob = TestClient(app)
    assert alice.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "alice-password"},
    ).status_code == 200
    assert bob.post(
        "/api/v1/auth/login",
        json={"username": "bob", "password": "bob-password"},
    ).status_code == 200

    alice_response = alice.post("/api/v1/navidrome/play-state/refresh")
    bob_response = bob.post("/api/v1/navidrome/play-state/refresh")

    assert alice_response.status_code == 200
    assert alice_response.json() == {
        "user": "alice",
        "seen_count": 4,
        "updated_count": 3,
        "unmapped_count": 1,
    }
    assert bob_response.status_code == 200
    assert [call[0] for call in calls] == [
        store.get_user_by_username("alice")["id"],
        store.get_user_by_username("bob")["id"],
    ]
    assert [call[1:3] for call in calls] == [
        ("alice", "alice-password"),
        ("bob", "bob-password"),
    ]
    assert [call[3] for call in calls] == [25, 25]


def test_play_state_refresh_reports_navidrome_failure(tmp_path, monkeypatch):
    _init_store(tmp_path, monkeypatch)
    monkeypatch.setattr(
        auth,
        "verify_navidrome_credentials",
        lambda _settings, _username, password, **_kwargs: password == "correct",
    )
    monkeypatch.setattr(
        auth, "sync_navidrome_starred_for_user", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr("app.api.deps.NavidromeClient", lambda _settings: object())

    def fail_refresh(*_args, **_kwargs):
        raise RuntimeError("recent endpoint unavailable")

    monkeypatch.setattr("app.navidrome_sync.refresh_navidrome_play_state", fail_refresh)
    client = _login("alice")

    response = client.post("/api/v1/navidrome/play-state/refresh")

    assert response.status_code == 502
    assert "recent endpoint unavailable" in response.json()["detail"]
