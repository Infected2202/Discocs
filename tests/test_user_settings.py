"""Per-user settings: store round-trip, API get/patch, validation, isolation."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app import auth
from app.main import app
from app.store import INITIALIZED_DB_PATHS, Store


def test_store_get_user_settings_defaults(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()

    assert store.get_user_settings() == {"language": "en"}


def test_store_set_user_settings_round_trip(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()

    result = store.set_user_settings({"language": "ru"})

    assert result == {"language": "ru"}
    assert store.get_user_settings() == {"language": "ru"}


def test_store_set_user_settings_overwrites_existing_key(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()

    store.set_user_settings({"language": "ru"})
    result = store.set_user_settings({"language": "en"})

    assert result == {"language": "en"}


def _init_api_store(tmp_path: Path, monkeypatch) -> Store:
    db_path = tmp_path / "app.db"
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    monkeypatch.setenv("DISCOCS_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.delenv("DISCOCS_AUTH_ENABLED", raising=False)
    store = Store(db_path)
    store.init()
    return store


def test_api_get_user_settings_defaults(tmp_path: Path, monkeypatch):
    _init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.get("/api/v1/me/settings")

    assert response.status_code == 200
    assert response.json() == {"language": "en"}


def test_api_patch_user_settings_updates_language(tmp_path: Path, monkeypatch):
    _init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.patch("/api/v1/me/settings", json={"language": "ru"})

    assert response.status_code == 200
    assert response.json() == {"language": "ru"}
    assert client.get("/api/v1/me/settings").json() == {"language": "ru"}


def test_api_patch_user_settings_rejects_unknown_language(tmp_path: Path, monkeypatch):
    _init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.patch("/api/v1/me/settings", json={"language": "fr"})

    assert response.status_code == 422
    assert client.get("/api/v1/me/settings").json() == {"language": "en"}


def test_api_patch_user_settings_rejects_unknown_field(tmp_path: Path, monkeypatch):
    _init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.patch("/api/v1/me/settings", json={"theme": "dark"})

    assert response.status_code == 422


def test_api_patch_user_settings_empty_body_is_a_noop(tmp_path: Path, monkeypatch):
    _init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.patch("/api/v1/me/settings", json={})

    assert response.status_code == 200
    assert response.json() == {"language": "en"}


def _init_multiuser_store(tmp_path: Path, monkeypatch) -> Store:
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


def test_user_settings_are_isolated_per_account(tmp_path: Path, monkeypatch):
    _init_multiuser_store(tmp_path, monkeypatch)
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

    alice_patch = alice.patch("/api/v1/me/settings", json={"language": "ru"})
    assert alice_patch.status_code == 200
    assert alice_patch.json() == {"language": "ru"}

    assert bob.get("/api/v1/me/settings").json() == {"language": "en"}
    assert alice.get("/api/v1/me/settings").json() == {"language": "ru"}
