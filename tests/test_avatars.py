"""Built-in avatars: whitelist ↔ UI files, lazy stable default, own-only change."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from fastapi.testclient import TestClient

import app.avatars as avatars_module
from app import auth
from app.avatars import AVATAR_KEYS, AVATAR_SETTING_KEY, ensure_user_avatar
from app.main import app
from app.models import utc_now
from app.store import INITIALIZED_DB_PATHS, Store

AVATAR_DIR = Path(__file__).resolve().parents[1] / "ui" / "src" / "assets" / "avatars"


def _sequence(*keys: str):
    values: Iterator[str] = iter(keys)
    return lambda: next(values)


def test_every_whitelisted_key_has_a_file_and_vice_versa():
    assert AVATAR_DIR.is_dir(), AVATAR_DIR
    assert {path.name for path in AVATAR_DIR.iterdir()} == {f"{key}.webp" for key in AVATAR_KEYS}


def test_default_avatar_is_assigned_once_and_stays(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "app.db")
    store.init()
    user_id = store.upsert_user("alice", now=utc_now())
    monkeypatch.setattr(avatars_module, "random_avatar_key", _sequence("a02", "a05", "a06"))

    first = ensure_user_avatar(store, user_id)
    second = ensure_user_avatar(store, user_id)

    assert first == second == "a02"
    assert store.get_user_avatar(user_id) == "a02"


def test_concurrent_first_assignment_keeps_the_first_key(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    user_id = store.upsert_user("alice", now=utc_now())

    assert store.assign_default_avatar(user_id, "a01") == "a01"
    # A racing reader that also saw "no avatar" must not overwrite it.
    assert store.assign_default_avatar(user_id, "a04") == "a01"


def test_avatar_outside_whitelist_is_replaced(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "app.db")
    store.init()
    user_id = store.upsert_user("alice", now=utc_now())
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO user_settings (user_id, key, value, updated_at) VALUES (?, ?, 'retired', ?)",
            (user_id, AVATAR_SETTING_KEY, utc_now()),
        )
    monkeypatch.setattr(avatars_module, "random_avatar_key", _sequence("a03"))

    assert ensure_user_avatar(store, user_id) == "a03"
    assert store.get_user_avatar(user_id) == "a03"


def test_avatar_is_not_part_of_user_settings(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    store.set_own_avatar("a04")

    assert AVATAR_SETTING_KEY not in store.get_user_settings()
    assert store.get_user_avatar(store.user_id) == "a04"


# ---------------------------------------------------------------------------
# API (auth enabled, two real sessions)
# ---------------------------------------------------------------------------

def _init_auth_store(tmp_path: Path, monkeypatch) -> Store:
    db_path = tmp_path / "app.db"
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    monkeypatch.setenv("DISCOCS_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("DISCOCS_AUTH_ENABLED", "true")
    monkeypatch.setenv("DISCOCS_NAVIDROME_URL", "http://navidrome:4533")
    monkeypatch.setenv("DISCOCS_SERVICE_TOKEN", "svc-secret")
    monkeypatch.setattr(
        auth,
        "verify_navidrome_credentials",
        lambda _settings, username, password, **_kwargs: (
            username in {"alice", "bob"} and password == "correct"
        ),
    )
    monkeypatch.setattr(auth, "sync_navidrome_starred_for_user", lambda *_args, **_kwargs: None)
    store = Store(db_path)
    store.init()
    return store


def _login(username: str) -> TestClient:
    client = TestClient(app)
    response = client.post("/api/v1/auth/login", json={"username": username, "password": "correct"})
    assert response.status_code == 200
    return client


def _user_id(store: Store, username: str) -> int:
    return int(store.get_user_by_username(username)["id"])


def test_login_assigns_an_avatar(tmp_path: Path, monkeypatch):
    store = _init_auth_store(tmp_path, monkeypatch)
    monkeypatch.setattr(avatars_module, "random_avatar_key", _sequence("a06", "a01"))

    _login("alice")
    _login("alice")

    assert store.get_user_avatar(_user_id(store, "alice")) == "a06"


def test_users_endpoint_exposes_only_username_and_avatar(tmp_path: Path, monkeypatch):
    store = _init_auth_store(tmp_path, monkeypatch)
    alice = _login("alice")
    _login("bob")

    first = alice.get("/api/v1/users")
    second = alice.get("/api/v1/users")

    assert first.status_code == 200
    items = {item["username"]: item for item in first.json()["items"]}
    assert {"alice", "bob"} <= set(items)
    for item in first.json()["items"]:
        assert set(item) == {"username", "avatar"}
        assert item["avatar"] in AVATAR_KEYS
    assert second.json() == first.json()
    assert items["bob"]["avatar"] == store.get_user_avatar(_user_id(store, "bob"))


def test_user_changes_only_their_own_avatar(tmp_path: Path, monkeypatch):
    store = _init_auth_store(tmp_path, monkeypatch)
    alice = _login("alice")
    bob = _login("bob")
    bob_before = store.get_user_avatar(_user_id(store, "bob"))
    new_key = next(key for key in AVATAR_KEYS if key != bob_before)

    response = alice.put("/api/v1/me/avatar", json={"key": new_key})

    assert response.status_code == 200
    assert response.json() == {"avatar": new_key}
    assert store.get_user_avatar(_user_id(store, "alice")) == new_key
    assert store.get_user_avatar(_user_id(store, "bob")) == bob_before
    listed = {item["username"]: item["avatar"] for item in bob.get("/api/v1/users").json()["items"]}
    assert listed["alice"] == new_key
    # There is no way to address someone else: extra fields are rejected.
    hijack = alice.put("/api/v1/me/avatar", json={"key": new_key, "username": "bob"})
    assert hijack.status_code == 422
    assert store.get_user_avatar(_user_id(store, "bob")) == bob_before


def test_avatar_outside_whitelist_is_rejected(tmp_path: Path, monkeypatch):
    store = _init_auth_store(tmp_path, monkeypatch)
    alice = _login("alice")
    before = store.get_user_avatar(_user_id(store, "alice"))

    for bad in ("a99", "../a01", "", "A01"):
        response = alice.put("/api/v1/me/avatar", json={"key": bad})
        assert response.status_code == 422, bad

    assert store.get_user_avatar(_user_id(store, "alice")) == before


def test_service_principal_cannot_use_user_endpoints(tmp_path: Path, monkeypatch):
    _init_auth_store(tmp_path, monkeypatch)
    client = TestClient(app)
    headers = {"X-Discocs-Service-Token": "svc-secret"}

    assert client.get("/api/v1/users", headers=headers).status_code == 403
    assert client.put("/api/v1/me/avatar", json={"key": "a01"}, headers=headers).status_code == 403
    assert client.get("/api/v1/users").status_code == 401
