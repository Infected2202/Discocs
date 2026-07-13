from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.config import NavidromeSettings, Settings
from app.main import app
from app.store import INITIALIZED_DB_PATHS, Store
from app.models import utc_now


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def init_store(tmp_path: Path, monkeypatch) -> Store:
    db_path = tmp_path / "app.db"
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    monkeypatch.setenv("DISCOCS_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_MODEL_DIR", str(tmp_path / "models"))
    store = Store(db_path)
    store.init()
    return store


def settings_with_navidrome(**overrides) -> Settings:
    nav = NavidromeSettings(url="http://navidrome:4533", user="", password="")
    base = Settings(
        data_dir=Path("data"),
        db_path=Path("data/app.db"),
        model_dir=Path("models"),
        index_dir=Path("data"),
        navidrome=nav,
    )
    return base


@pytest.fixture(autouse=True)
def _reset_login_limiter():
    from app.api import auth as auth_api

    auth_api._limiter._failures.clear()
    yield
    auth_api._limiter._failures.clear()


# ---------------------------------------------------------------------------
# Session store round-trip
# ---------------------------------------------------------------------------

def test_session_store_roundtrip(tmp_path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    now = utc_now()
    future = (datetime.fromisoformat(now) + timedelta(hours=1)).isoformat()
    store.create_session(
        token_hash="abc",
        username="alice",
        created_at=now,
        expires_at=future,
        ip="1.2.3.4",
        user_agent="pytest",
    )
    row = store.get_session("abc")
    assert row is not None
    assert row["username"] == "alice"
    assert row["ip"] == "1.2.3.4"

    store.delete_session("abc")
    assert store.get_session("abc") is None


def test_purge_expired_sessions(tmp_path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    now = datetime.fromisoformat(utc_now())
    store.create_session(
        token_hash="old",
        username="a",
        created_at=(now - timedelta(hours=2)).isoformat(),
        expires_at=(now - timedelta(hours=1)).isoformat(),
        ip=None,
        user_agent=None,
    )
    store.create_session(
        token_hash="live",
        username="a",
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
        ip=None,
        user_agent=None,
    )
    purged = store.purge_expired_sessions(now.isoformat())
    assert purged == 1
    assert store.get_session("old") is None
    assert store.get_session("live") is not None


# ---------------------------------------------------------------------------
# auth.resolve_session
# ---------------------------------------------------------------------------

def test_resolve_session_valid_and_expired(tmp_path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    now = datetime.fromisoformat(utc_now())

    valid_token = "valid-token"
    store.create_session(
        token_hash=auth.hash_token(valid_token),
        username="bob",
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
        ip=None,
        user_agent=None,
    )
    resolved = auth.resolve_session(store, valid_token)
    assert resolved is not None
    assert resolved.username == "bob"
    # Legacy session (created without a users row) → user_id unresolved.
    assert resolved.user_id is None

    expired_token = "expired-token"
    store.create_session(
        token_hash=auth.hash_token(expired_token),
        username="bob",
        created_at=(now - timedelta(hours=2)).isoformat(),
        expires_at=(now - timedelta(hours=1)).isoformat(),
        ip=None,
        user_agent=None,
    )
    assert auth.resolve_session(store, expired_token) is None
    # Expired session is deleted lazily on lookup.
    assert store.get_session(auth.hash_token(expired_token)) is None
    assert auth.resolve_session(store, None) is None
    assert auth.resolve_session(store, "never-existed") is None


# ---------------------------------------------------------------------------
# Users: upsert + session binding (Phase 2 identity)
# ---------------------------------------------------------------------------

def test_upsert_user_is_idempotent_and_bumps_last_login(tmp_path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    first = store.upsert_user("alice", now="2026-01-01T00:00:00")
    again = store.upsert_user("alice", now="2026-02-02T00:00:00")
    # Same username → same internal id, no duplicate row.
    assert first == again
    row = store.get_user_by_username("alice")
    assert row["id"] == first
    assert row["created_at"] == "2026-01-01T00:00:00"
    assert row["last_login_at"] == "2026-02-02T00:00:00"
    # A different username gets a distinct id.
    bob = store.upsert_user("bob", now="2026-01-01T00:00:00")
    assert bob != first
    assert store.get_user_by_id(bob)["navidrome_username"] == "bob"


def test_upsert_user_rejects_empty(tmp_path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        store.upsert_user("", now=utc_now())


def test_create_session_binds_user_id(tmp_path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    settings = settings_with_navidrome()
    token = auth.create_session(store, settings, "carol", "nav-password")

    resolved = auth.resolve_session(store, token)
    assert resolved is not None
    assert resolved.username == "carol"
    # Login upserted the user and bound its id to the session.
    expected = store.get_user_by_username("carol")["id"]
    assert resolved.user_id == expected
    # The id is persisted on the session row, not only re-derived.
    assert store.get_session(auth.hash_token(token))["user_id"] == expected
    row = store.get_session(auth.hash_token(token))
    assert row["nav_secret"]
    assert "nav-password" not in row["nav_secret"]
    assert resolved.navidrome_password == "nav-password"


def test_resolve_session_recovers_user_id_for_legacy_row(tmp_path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    now = datetime.fromisoformat(utc_now())
    user_id = store.upsert_user("dave", now=now.isoformat())
    # Legacy session written without user_id (NULL).
    token = "legacy-token"
    store.create_session(
        token_hash=auth.hash_token(token),
        username="dave",
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
        ip=None,
        user_agent=None,
    )
    assert store.get_session(auth.hash_token(token))["user_id"] is None
    resolved = auth.resolve_session(store, token)
    # Falls back to the users table so the id is still recovered.
    assert resolved.user_id == user_id


def test_login_star_sync_is_bound_to_authenticated_user():
    root = MagicMock()
    scoped = MagicMock()
    root.for_user.return_value = scoped
    scoped.get_track_by_external_id.return_value = SimpleNamespace(id=7)
    scoped.entity_id_for_external_id.return_value = 9

    class FakeClient:
        def __init__(self, settings):
            assert settings.user == "alice"
            assert settings.password == "secret"

        def get_starred_full(self):
            return {
                "songs": [{"id": "song-7", "title": "Seven"}],
                "albums": [],
                "artists": [{"id": "artist-9"}],
            }

    auth.sync_navidrome_starred_for_user(
        root,
        settings_with_navidrome(),
        42,
        "alice",
        "secret",
        client_factory=FakeClient,
    )

    root.for_user.assert_called_once_with(42)
    scoped.sync_track_liked_from_navidrome.assert_called_once_with([7])
    scoped.sync_artist_liked_from_navidrome.assert_called_once_with([9])


# ---------------------------------------------------------------------------
# Credential verification against Navidrome
# ---------------------------------------------------------------------------

def test_verify_navidrome_credentials():
    settings = settings_with_navidrome()

    class OkClient:
        def __init__(self, nav):
            self.nav = nav

        def ping(self):
            # The submitted creds must be threaded into the client settings.
            assert self.nav.user == "alice"
            assert self.nav.password == "pw"
            return {}

    class FailClient:
        def __init__(self, nav):
            pass

        def ping(self):
            raise RuntimeError("wrong password")

    assert auth.verify_navidrome_credentials(
        settings, "alice", "pw", client_factory=OkClient
    ) is True
    assert auth.verify_navidrome_credentials(
        settings, "alice", "bad", client_factory=FailClient
    ) is False
    # Empty inputs short-circuit without touching the client.
    assert auth.verify_navidrome_credentials(settings, "", "pw") is False
    assert auth.verify_navidrome_credentials(settings, "alice", "") is False


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

def test_login_rate_limiter_lockout_and_reset():
    limiter = auth.LoginRateLimiter(max_attempts=3, window_seconds=100)
    assert limiter.is_locked("ip", now=0.0) is False
    limiter.record_failure("ip", now=0.0)
    limiter.record_failure("ip", now=1.0)
    assert limiter.is_locked("ip", now=2.0) is False
    limiter.record_failure("ip", now=2.0)
    assert limiter.is_locked("ip", now=3.0) is True
    # Window slides — old failures age out.
    assert limiter.is_locked("ip", now=200.0) is False
    # Success clears the counter immediately.
    limiter.record_failure("ip", now=201.0)
    limiter.record_failure("ip", now=202.0)
    limiter.record_failure("ip", now=203.0)
    assert limiter.is_locked("ip", now=204.0) is True
    limiter.record_success("ip")
    assert limiter.is_locked("ip", now=205.0) is False


# ---------------------------------------------------------------------------
# API — gate disabled (default) leaves everything open
# ---------------------------------------------------------------------------

def test_gate_disabled_is_open(tmp_path, monkeypatch):
    init_store(tmp_path, monkeypatch)
    monkeypatch.delenv("DISCOCS_AUTH_ENABLED", raising=False)
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    session = client.get("/api/v1/auth/session").json()
    assert session == {"authenticated": True, "username": None, "enabled": False}
    # A normally-protected route stays reachable with no session.
    assert client.get("/api/v1/settings/navidrome").status_code == 200


# ---------------------------------------------------------------------------
# API — gate enabled
# ---------------------------------------------------------------------------

def _enable_gate(monkeypatch):
    monkeypatch.setenv("DISCOCS_AUTH_ENABLED", "true")
    monkeypatch.setenv("DISCOCS_NAVIDROME_URL", "http://navidrome:4533")
    monkeypatch.setenv("DISCOCS_SERVICE_TOKEN", "svc-secret")

    def fake_verify(settings, username, password, **kwargs):
        return username == "alice" and password == "correct"

    monkeypatch.setattr(auth, "verify_navidrome_credentials", fake_verify)
    monkeypatch.setattr(
        auth,
        "sync_navidrome_starred_for_user",
        lambda *_args, **_kwargs: None,
    )


def test_gate_blocks_anonymous_but_allows_public(tmp_path, monkeypatch):
    init_store(tmp_path, monkeypatch)
    _enable_gate(monkeypatch)
    client = TestClient(app)

    # Public paths remain reachable.
    assert client.get("/health").status_code == 200
    session = client.get("/api/v1/auth/session").json()
    assert session["authenticated"] is False
    assert session["enabled"] is True

    # Protected route rejected without a session.
    resp = client.get("/api/v1/settings/navidrome")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_login_bad_then_good_creds(tmp_path, monkeypatch):
    init_store(tmp_path, monkeypatch)
    _enable_gate(monkeypatch)
    client = TestClient(app)

    bad = client.post("/api/v1/auth/login", json={"username": "alice", "password": "wrong"})
    assert bad.status_code == 401

    good = client.post("/api/v1/auth/login", json={"username": "alice", "password": "correct"})
    assert good.status_code == 200
    assert good.json()["authenticated"] is True
    # Session cookie issued and now grants access to protected routes.
    assert client.cookies.get("discocs_session")
    assert client.get("/api/v1/settings/navidrome").status_code == 200
    assert client.get("/api/v1/auth/session").json()["username"] == "alice"


def test_login_creates_user_and_binds_session(tmp_path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    _enable_gate(monkeypatch)
    client = TestClient(app)

    assert store.get_user_by_username("alice") is None
    client.post("/api/v1/auth/login", json={"username": "alice", "password": "correct"})

    user = store.get_user_by_username("alice")
    assert user is not None
    token = client.cookies.get("discocs_session")
    assert store.get_session(auth.hash_token(token))["user_id"] == user["id"]


def test_service_token_grants_access(tmp_path, monkeypatch):
    init_store(tmp_path, monkeypatch)
    _enable_gate(monkeypatch)
    client = TestClient(app)

    # No session, but a valid machine token works (workers/bot/plugin path).
    ok = client.get("/api/v1/settings/navidrome", headers={"X-Discocs-Service-Token": "svc-secret"})
    assert ok.status_code == 200
    # Wrong token is rejected.
    bad = client.get("/api/v1/settings/navidrome", headers={"X-Discocs-Service-Token": "nope"})
    assert bad.status_code == 401


def test_logout_revokes_session(tmp_path, monkeypatch):
    init_store(tmp_path, monkeypatch)
    _enable_gate(monkeypatch)
    client = TestClient(app)

    client.post("/api/v1/auth/login", json={"username": "alice", "password": "correct"})
    assert client.get("/api/v1/settings/navidrome").status_code == 200

    client.post("/api/v1/auth/logout")
    # Cookie cleared client-side; session revoked server-side.
    assert client.get("/api/v1/settings/navidrome").status_code == 401


def test_login_lockout_after_max_attempts(tmp_path, monkeypatch):
    init_store(tmp_path, monkeypatch)
    _enable_gate(monkeypatch)
    monkeypatch.setenv("DISCOCS_LOGIN_MAX_ATTEMPTS", "3")
    client = TestClient(app)

    for _ in range(3):
        resp = client.post("/api/v1/auth/login", json={"username": "alice", "password": "wrong"})
        assert resp.status_code == 401

    locked = client.post("/api/v1/auth/login", json={"username": "alice", "password": "correct"})
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "too_many_attempts"
