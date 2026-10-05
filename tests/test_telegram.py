from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import auth, telegram_bot
from app.api import telegram as telegram_api
from app.config import Settings
from app.main import app
from app.models import utc_now
from app.store import INITIALIZED_DB_PATHS, Store
from app.store.telegram import telegram_link_token_hash

SERVICE_TOKEN = "svc-secret"


@pytest.fixture(autouse=True)
def _fresh_username_cache():
    telegram_bot.clear_username_cache()
    yield
    telegram_bot.clear_username_cache()


def _init_store(tmp_path: Path, monkeypatch, *, bot_url: str = "http://awg:8090") -> Store:
    db_path = tmp_path / "app.db"
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    monkeypatch.setenv("DISCOCS_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("DISCOCS_AUTH_ENABLED", "true")
    monkeypatch.setenv("DISCOCS_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("DISCOCS_BOT_URL", bot_url)
    monkeypatch.delenv("DISCOCS_PUBLIC_URL", raising=False)
    store = Store(db_path)
    store.init()
    return store


def _user_store(store: Store, username: str) -> Store:
    user_id = store.upsert_user(username, now=utc_now())
    scoped = Store(store.db_path, user_id=user_id)
    scoped.init()
    return scoped


def _session_client(store: Store, username: str = "alice") -> TestClient:
    token = auth.create_session(store, Settings.from_env(), username, "nav-password")
    client = TestClient(app)
    client.cookies.set("discocs_session", token)
    return client


def _service_client() -> TestClient:
    return TestClient(app, headers={"X-Discocs-Service-Token": SERVICE_TOKEN})


def _track(store: Store, *, title: str, navidrome_id: str | None) -> int:
    now = utc_now()
    with store.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO tracks (
                path, artist, title, album, duration, file_size, mtime,
                created_at, updated_at
            ) VALUES (?, 'Artist', ?, 'Album', 200.0, 1, 1, ?, ?)
            """,
            (f"/music/{title}.flac", title, now, now),
        )
        track_id = int(cursor.lastrowid)
        if navidrome_id:
            conn.execute(
                "INSERT INTO external_tracks (provider, external_id, track_id, synced_at) "
                "VALUES ('navidrome', ?, ?, ?)",
                (navidrome_id, track_id, now),
            )
    return track_id


def _release(store: Store, *, title: str, navidrome_album_id: str | None) -> int:
    now = utc_now()
    with store.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO releases (
                title, normalized_title, release_type, track_count,
                identity_key, identity_confidence, created_at, updated_at
            ) VALUES (?, ?, 'album', 0, ?, 'exact', ?, ?)
            """,
            (title, title.casefold(), f"test:{title}", now, now),
        )
        release_id = int(cursor.lastrowid)
        if navidrome_album_id:
            conn.execute(
                "INSERT INTO external_ids (provider, entity_type, entity_id, external_id, synced_at) "
                "VALUES ('navidrome', 'release', ?, ?, ?)",
                (release_id, navidrome_album_id, now),
            )
    return release_id


def _link(store: Store, username: str, telegram_user_id: int) -> None:
    scoped = _user_store(store, username)
    token, _expires = scoped.create_telegram_link_token(ttl_minutes=10)
    assert store.redeem_telegram_link_token(
        token, telegram_user_id=telegram_user_id, telegram_username=None
    ) == username


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def test_link_token_is_hashed_single_use_and_binds_the_minting_user(tmp_path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    alice = _user_store(store, "alice")

    token, expires_at = alice.create_telegram_link_token(ttl_minutes=10)

    with store.connect() as conn:
        stored = [row["token_hash"] for row in conn.execute("SELECT token_hash FROM telegram_link_tokens")]
    assert stored == [telegram_link_token_hash(token)]
    assert token not in stored
    assert datetime.fromisoformat(expires_at) > datetime.now(UTC)

    assert store.redeem_telegram_link_token(token, telegram_user_id=111, telegram_username="al") == "alice"
    link = alice.get_own_telegram_link()
    assert (link["telegram_user_id"], link["telegram_username"]) == (111, "al")

    # Second redemption of the same token is refused and changes nothing.
    assert store.redeem_telegram_link_token(token, telegram_user_id=222, telegram_username=None) is None
    assert alice.get_own_telegram_link()["telegram_user_id"] == 111


def test_expired_token_is_refused_and_consumed(tmp_path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    alice = _user_store(store, "alice")
    token, _ = alice.create_telegram_link_token(ttl_minutes=10)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    with store.connect() as conn:
        conn.execute("UPDATE telegram_link_tokens SET expires_at = ?", (past,))

    assert store.redeem_telegram_link_token(token, telegram_user_id=111, telegram_username=None) is None
    assert alice.get_own_telegram_link() is None
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM telegram_link_tokens").fetchone()[0] == 0


def test_new_token_invalidates_the_previous_one(tmp_path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    alice = _user_store(store, "alice")
    first, _ = alice.create_telegram_link_token(ttl_minutes=10)
    second, _ = alice.create_telegram_link_token(ttl_minutes=10)

    assert store.redeem_telegram_link_token(first, telegram_user_id=111, telegram_username=None) is None
    assert store.redeem_telegram_link_token(second, telegram_user_id=111, telegram_username=None) == "alice"


def test_relinking_moves_the_telegram_account_and_replaces_the_old_one(tmp_path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    _link(store, "alice", 111)
    _link(store, "bob", 111)
    alice = _user_store(store, "alice")
    bob = _user_store(store, "bob")

    # One Telegram account belongs to one user: it moved from alice to bob.
    assert alice.get_own_telegram_link() is None
    assert bob.get_own_telegram_link()["telegram_user_id"] == 111

    # A user has one Telegram account: linking another replaces it.
    _link(store, "bob", 222)
    assert bob.get_own_telegram_link()["telegram_user_id"] == 222
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM telegram_links").fetchone()[0] == 1


def test_telegram_settings_follow_env(monkeypatch):
    monkeypatch.delenv("DISCOCS_BOT_URL", raising=False)
    assert Settings.from_env().telegram.enabled is False
    monkeypatch.setenv("DISCOCS_BOT_URL", "http://awg:8090/")
    telegram = Settings.from_env().telegram
    assert telegram.enabled is True
    assert telegram.bot_url == "http://awg:8090"


# ---------------------------------------------------------------------------
# Linking API
# ---------------------------------------------------------------------------


def test_link_flow_from_deep_link_to_unlink(tmp_path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    monkeypatch.setattr(telegram_api, "bot_username", lambda _settings: "discocs_bot")
    client = _session_client(store)

    status = client.get("/api/v1/telegram/link").json()
    assert status == {"enabled": True, "linked": False, "link": None}

    started = client.post("/api/v1/telegram/link").json()
    prefix = "https://t.me/discocs_bot?start=link_"
    assert started["url"].startswith(prefix)
    token = started["url"][len(prefix):]

    redeemed = _service_client().post(
        "/api/v1/telegram/link/redeem",
        json={"token": token, "telegram_user_id": 555, "telegram_username": "alice_tg"},
    )
    assert redeemed.status_code == 200
    assert redeemed.json() == {"username": "alice"}

    status = client.get("/api/v1/telegram/link").json()
    assert status["linked"] is True
    assert status["link"]["telegram_username"] == "alice_tg"

    assert client.delete("/api/v1/telegram/link").status_code == 204
    assert client.get("/api/v1/telegram/link").json()["linked"] is False


def test_redeem_is_service_only_and_rejects_unknown_tokens(tmp_path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    alice = _user_store(store, "alice")
    token, _ = alice.create_telegram_link_token(ttl_minutes=10)
    body = {"token": token, "telegram_user_id": 555}

    # A signed-in user cannot redeem on behalf of an arbitrary Telegram id.
    as_user = _session_client(store, "bob").post("/api/v1/telegram/link/redeem", json=body)
    assert as_user.status_code == 403
    assert alice.get_own_telegram_link() is None

    unknown = _service_client().post(
        "/api/v1/telegram/link/redeem", json={**body, "token": "x" * 32}
    )
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "link_token_invalid"


def test_user_endpoints_refuse_the_service_principal(tmp_path, monkeypatch):
    _init_store(tmp_path, monkeypatch)
    service = _service_client()
    assert service.get("/api/v1/telegram/link").status_code == 401
    assert service.post("/api/v1/telegram/link").status_code == 401
    assert service.post(
        "/api/v1/telegram/send", json={"source_type": "track", "source_id": 1}
    ).status_code == 401


def test_start_link_reports_disabled_and_unreachable_bot(tmp_path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch, bot_url="")
    client = _session_client(store)
    assert client.get("/api/v1/telegram/link").json()["enabled"] is False
    assert client.post("/api/v1/telegram/link").status_code == 404

    monkeypatch.setenv("DISCOCS_BOT_URL", "http://awg:8090")

    def down(_settings):
        raise telegram_bot.TelegramBotUnavailable("connection refused")

    monkeypatch.setattr(telegram_api, "bot_username", down)
    response = client.post("/api/v1/telegram/link")
    assert response.status_code == 503
    # No token is minted when the deep link cannot be built.
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM telegram_link_tokens").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(telegram_api, "send_to_bot", lambda _settings, payload: calls.append(payload))
    return calls


def test_send_requires_a_linked_account(tmp_path, monkeypatch, sent):
    store = _init_store(tmp_path, monkeypatch)
    track_id = _track(store, title="Song", navidrome_id="nd-song")
    response = _session_client(store).post(
        "/api/v1/telegram/send", json={"source_type": "track", "source_id": track_id}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "telegram_not_linked"
    assert sent == []


def test_send_track_hands_the_bot_the_navidrome_song_of_the_own_chat(tmp_path, monkeypatch, sent):
    store = _init_store(tmp_path, monkeypatch)
    _link(store, "alice", 555)
    _link(store, "bob", 777)
    track_id = _track(store, title="Song", navidrome_id="nd-song")

    response = _session_client(store, "alice").post(
        "/api/v1/telegram/send", json={"source_type": "track", "source_id": track_id}
    )

    assert response.status_code == 202
    assert sent == [{"telegram_user_id": 555, "kind": "track", "navidrome_id": "nd-song"}]


def test_send_release_hands_the_bot_the_album_and_an_open_link(tmp_path, monkeypatch, sent):
    store = _init_store(tmp_path, monkeypatch)
    _link(store, "alice", 555)
    release_id = _release(store, title="Record", navidrome_album_id="nd-album")

    response = _session_client(store).post(
        "/api/v1/telegram/send", json={"source_type": "release", "source_id": release_id}
    )

    assert response.status_code == 202
    assert sent == [
        {
            "telegram_user_id": 555,
            "kind": "release",
            "navidrome_id": "nd-album",
            "open_url": f"http://testserver/releases/{release_id}",
        }
    ]


def test_send_refuses_items_without_navidrome_source(tmp_path, monkeypatch, sent):
    store = _init_store(tmp_path, monkeypatch)
    _link(store, "alice", 555)
    client = _session_client(store)
    track_id = _track(store, title="Local", navidrome_id=None)
    release_id = _release(store, title="Local record", navidrome_album_id=None)

    for source_type, source_id in (("track", track_id), ("release", release_id)):
        response = client.post(
            "/api/v1/telegram/send", json={"source_type": source_type, "source_id": source_id}
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "not_in_navidrome"
    assert client.post(
        "/api/v1/telegram/send", json={"source_type": "track", "source_id": 9999}
    ).status_code == 404
    assert sent == []


def test_send_maps_bot_failures(tmp_path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    _link(store, "alice", 555)
    track_id = _track(store, title="Song", navidrome_id="nd-song")
    client = _session_client(store)
    body = {"source_type": "track", "source_id": track_id}

    def blocked(_settings, _payload):
        raise telegram_bot.TelegramBotRejected("chat_unavailable")

    monkeypatch.setattr(telegram_api, "send_to_bot", blocked)
    response = client.post("/api/v1/telegram/send", json=body)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "telegram_chat_unavailable")

    def down(_settings, _payload):
        raise telegram_bot.TelegramBotUnavailable("timeout")

    monkeypatch.setattr(telegram_api, "send_to_bot", down)
    response = client.post("/api/v1/telegram/send", json=body)
    assert (response.status_code, response.json()["error"]["code"]) == (503, "telegram_bot_unavailable")


# ---------------------------------------------------------------------------
# Bot client
# ---------------------------------------------------------------------------


def _settings_with_bot(monkeypatch) -> Settings:
    monkeypatch.setenv("DISCOCS_BOT_URL", "http://awg:8090")
    monkeypatch.setenv("DISCOCS_SERVICE_TOKEN", SERVICE_TOKEN)
    return Settings.from_env()


def test_bot_client_sends_service_token_and_caches_username(monkeypatch):
    settings = _settings_with_bot(monkeypatch)
    requests: list[tuple[str, str, dict]] = []

    def fake_request(method, url, *, json=None, headers=None, timeout=None):
        requests.append((method, url, headers))
        return httpx.Response(200, json={"username": "discocs_bot"})

    monkeypatch.setattr(telegram_bot.httpx, "request", fake_request)

    assert telegram_bot.bot_username(settings) == "discocs_bot"
    assert telegram_bot.bot_username(settings) == "discocs_bot"
    assert len(requests) == 1
    method, url, headers = requests[0]
    assert (method, url) == ("GET", "http://awg:8090/internal/info")
    assert headers == {"X-Discocs-Service-Token": SERVICE_TOKEN}


def test_bot_client_error_mapping(monkeypatch):
    settings = _settings_with_bot(monkeypatch)
    responses = iter(
        [
            httpx.Response(409, json={"error": {"code": "chat_unavailable"}}),
            httpx.Response(401, json={"error": {"code": "unauthorized"}}),
            httpx.Response(502, text="bad gateway"),
        ]
    )
    monkeypatch.setattr(
        telegram_bot.httpx, "request", lambda *_args, **_kwargs: next(responses)
    )

    with pytest.raises(telegram_bot.TelegramBotRejected) as rejected:
        telegram_bot.send_to_bot(settings, {})
    assert rejected.value.code == "chat_unavailable"
    # A token mismatch is a deployment fault, not something the user can fix.
    with pytest.raises(telegram_bot.TelegramBotUnavailable):
        telegram_bot.send_to_bot(settings, {})
    with pytest.raises(telegram_bot.TelegramBotUnavailable):
        telegram_bot.send_to_bot(settings, {})

    def refused(*_args, **_kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(telegram_bot.httpx, "request", refused)
    with pytest.raises(telegram_bot.TelegramBotUnavailable):
        telegram_bot.send_to_bot(settings, {})
