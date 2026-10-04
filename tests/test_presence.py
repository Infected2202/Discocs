"""Presence (social Ф2): reportPlayback write path and the people/now-playing read path."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import app.services.presence as presence_module
from app import auth
from app.avatars import AVATAR_KEYS
from app.config import NavidromeSettings
from app.main import app
from app.navidrome import NavidromeClient, NowPlayingEntry
from app.scanner import ScannedTrack
from app.services.presence import NowPlayingCache
from app.store import INITIALIZED_DB_PATHS, Store

USERS = {"alice", "bob", "carol", "dave", "erin"}


# ---------------------------------------------------------------------------
# NavidromeClient: reportPlayback / getNowPlaying wire format
# ---------------------------------------------------------------------------

class _Response:
    def __init__(self, payload: dict):
        self._raw = json.dumps({"subsonic-response": {"status": "ok", **payload}}).encode()
        self.headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return self._raw


def _nav_settings() -> NavidromeSettings:
    return NavidromeSettings(url="http://navidrome:4533", user="alice", password="secret")


def test_report_playback_sends_opensubsonic_params():
    seen: list[str] = []

    def opener(request, timeout):
        seen.append(request.full_url)
        return _Response({})

    NavidromeClient(_nav_settings(), opener=opener).report_playback(
        "song-1", state="paused", position_ms=61234.9
    )

    url = urlparse(seen[0])
    query = parse_qs(url.query)
    assert url.path == "/rest/reportPlayback.view"
    assert query["mediaId"] == ["song-1"]
    assert query["mediaType"] == ["song"]
    assert query["state"] == ["paused"]
    assert query["positionMs"] == ["61234"]
    assert query["playbackRate"] == ["1.0"]
    assert query["ignoreScrobble"] == ["true"]


def test_report_playback_rejects_unknown_state():
    seen: list[str] = []

    def opener(request, timeout):
        seen.append(request.full_url)
        return _Response({})

    client = NavidromeClient(_nav_settings(), opener=opener)
    with pytest.raises(ValueError):
        client.report_playback("song-1", state="buffering", position_ms=0)
    assert seen == []


def test_get_now_playing_parses_entries_and_tolerates_missing_fields():
    payload = {
        "nowPlaying": {
            "entry": [
                {
                    "id": "song-1", "title": "One", "artist": "Alpha", "username": "bob",
                    "minutesAgo": 2, "playerName": "discocs", "state": "Playing",
                    "positionMs": 1500,
                },
                {"id": "song-2", "username": "carol"},
                {"title": "no id — dropped"},
            ]
        }
    }
    entries = NavidromeClient(
        _nav_settings(), opener=lambda *_a, **_k: _Response(payload)
    ).get_now_playing()

    assert [entry.id for entry in entries] == ["song-1", "song-2"]
    first, second = entries
    assert (first.username, first.title, first.artist) == ("bob", "One", "Alpha")
    assert (first.state, first.position_ms, first.minutes_ago, first.player_name) == (
        "playing", 1500, 2, "discocs",
    )
    assert (second.state, second.position_ms, second.minutes_ago, second.title) == (
        None, None, None, None,
    )


def test_get_now_playing_empty_payload():
    client = NavidromeClient(_nav_settings(), opener=lambda *_a, **_k: _Response({"nowPlaying": {}}))
    assert client.get_now_playing() == []


# ---------------------------------------------------------------------------
# API fixtures (auth enabled, real sessions)
# ---------------------------------------------------------------------------

def _init(tmp_path: Path, monkeypatch, *, service_account: bool = True) -> Store:
    db_path = tmp_path / "app.db"
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    monkeypatch.setenv("DISCOCS_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("DISCOCS_AUTH_ENABLED", "true")
    monkeypatch.setenv("DISCOCS_SERVICE_TOKEN", "svc-secret")
    monkeypatch.setenv("DISCOCS_NAVIDROME_URL", "http://navidrome:4533")
    if service_account:
        monkeypatch.setenv("DISCOCS_NAVIDROME_USER", "svc")
        monkeypatch.setenv("DISCOCS_NAVIDROME_PASSWORD", "svc-pass")
    else:
        monkeypatch.delenv("DISCOCS_NAVIDROME_USER", raising=False)
        monkeypatch.delenv("DISCOCS_NAVIDROME_PASSWORD", raising=False)
    monkeypatch.setattr(
        auth,
        "verify_navidrome_credentials",
        lambda _settings, username, password, **_kwargs: username in USERS and password == "correct",
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


def _track(store: Store, tmp_path: Path, title: str, artist: str) -> int:
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=(tmp_path / f"{title}.flac").resolve(),
            artist=artist,
            title=title,
            album="Presence",
            duration=200.0,
            file_size=1,
            mtime=1,
        )
    )
    return track_id


def _row_count(store: Store, table: str) -> int:
    with store.connect() as conn:
        return int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])  # noqa: S608


class _PresenceFake:
    """Records every reportPlayback with the settings the client was built with."""

    def __init__(self, *, fail: bool = False):
        self.calls: list[dict[str, object]] = []
        self.fail = fail

    def factory(self):
        fake = self

        class FakeClient:
            def __init__(self, settings):
                self.settings = settings

            def report_playback(self, media_id, *, state, position_ms, playback_rate=1.0, ignore_scrobble=True):
                if fake.fail:
                    raise RuntimeError("navidrome down")
                fake.calls.append(
                    {
                        "user": self.settings.user,
                        "password": self.settings.password,
                        "auth_mode": self.settings.auth_mode,
                        "media_id": media_id,
                        "state": state,
                        "position_ms": position_ms,
                        "ignore_scrobble": ignore_scrobble,
                    }
                )
                return {}

        return FakeClient


# ---------------------------------------------------------------------------
# POST /playback/presence
# ---------------------------------------------------------------------------

def test_presence_reports_with_session_credentials(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch)
    track_id = _track(store, tmp_path, "Signals", "Alpha")
    store.upsert_external_track("navidrome", "nav-song-1", track_id)
    fake = _PresenceFake()
    monkeypatch.setattr(presence_module, "NavidromeClient", fake.factory())
    alice = _login("alice")

    response = alice.post(
        "/api/v1/playback/presence",
        json={"track_id": track_id, "state": "paused", "position_ms": 42000},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert fake.calls == [
        {
            "user": "alice",
            "password": "correct",
            "auth_mode": "token",
            "media_id": "nav-song-1",
            "state": "paused",
            "position_ms": 42000,
            "ignore_scrobble": True,
        }
    ]


def test_presence_writes_no_playback_event_or_listen(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch)
    track_id = _track(store, tmp_path, "Signals", "Alpha")
    store.upsert_external_track("navidrome", "nav-song-1", track_id)
    monkeypatch.setattr(presence_module, "NavidromeClient", _PresenceFake().factory())
    alice = _login("alice")

    for state in ("starting", "playing", "paused", "stopped"):
        response = alice.post(
            "/api/v1/playback/presence",
            json={"track_id": track_id, "state": state, "position_ms": 200_000},
        )
        assert response.status_code == 200

    assert _row_count(store, "playback_events") == 0
    assert _row_count(store, "listens") == 0


def test_presence_skips_unmapped_track(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch)
    track_id = _track(store, tmp_path, "Local Only", "Alpha")
    fake = _PresenceFake()
    monkeypatch.setattr(presence_module, "NavidromeClient", fake.factory())
    alice = _login("alice")

    response = alice.post(
        "/api/v1/playback/presence",
        json={"track_id": track_id, "state": "starting", "position_ms": 0},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "skipped",
        "reason": "no_navidrome_mapping",
        "track_id": track_id,
    }
    assert fake.calls == []


def test_presence_navidrome_failure_is_200_failed(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch)
    track_id = _track(store, tmp_path, "Signals", "Alpha")
    store.upsert_external_track("navidrome", "nav-song-1", track_id)
    monkeypatch.setattr(presence_module, "NavidromeClient", _PresenceFake(fail=True).factory())
    alice = _login("alice")

    response = alice.post(
        "/api/v1/playback/presence",
        json={"track_id": track_id, "state": "playing", "position_ms": 1000},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"


def test_presence_requires_a_user_and_valid_body(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch)
    track_id = _track(store, tmp_path, "Signals", "Alpha")
    store.upsert_external_track("navidrome", "nav-song-1", track_id)
    fake = _PresenceFake()
    monkeypatch.setattr(presence_module, "NavidromeClient", fake.factory())
    body = {"track_id": track_id, "state": "playing", "position_ms": 0}

    service = TestClient(app).post(
        "/api/v1/playback/presence", json=body, headers={"X-Discocs-Service-Token": "svc-secret"}
    )
    anonymous = TestClient(app).post("/api/v1/playback/presence", json=body)
    assert service.status_code == 403
    assert anonymous.status_code == 401

    alice = _login("alice")
    for bad in (
        {**body, "state": "buffering"},
        {**body, "position_ms": -1},
        {**body, "user": "bob"},
        {"state": "playing", "position_ms": 0},
    ):
        assert alice.post("/api/v1/playback/presence", json=bad).status_code == 422, bad
    assert fake.calls == []


def test_presence_accepts_same_origin_beacon_and_rejects_cross_origin(tmp_path: Path, monkeypatch):
    # navigator.sendBeacon sends a same-origin POST with an Origin header and
    # the session cookie; the CSRF gate must let it through.
    store = _init(tmp_path, monkeypatch)
    track_id = _track(store, tmp_path, "Signals", "Alpha")
    store.upsert_external_track("navidrome", "nav-song-1", track_id)
    fake = _PresenceFake()
    monkeypatch.setattr(presence_module, "NavidromeClient", fake.factory())
    alice = _login("alice")
    payload = json.dumps({"track_id": track_id, "state": "stopped", "position_ms": 5000})

    same_origin = alice.post(
        "/api/v1/playback/presence",
        content=payload,
        headers={"Origin": "http://testserver", "Content-Type": "application/json"},
    )
    cross_origin = alice.post(
        "/api/v1/playback/presence",
        content=payload,
        headers={"Origin": "http://evil.example", "Content-Type": "application/json"},
    )

    assert same_origin.status_code == 200
    assert cross_origin.status_code == 403
    assert [call["state"] for call in fake.calls] == ["stopped"]


# ---------------------------------------------------------------------------
# GET /social/people
# ---------------------------------------------------------------------------

class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _NowPlayingFake:
    def __init__(self, entries: list[NowPlayingEntry] | None = None, *, fail: bool = False):
        self.entries = entries or []
        self.fail = fail
        self.calls: list[str] = []

    def factory(self):
        fake = self

        class FakeClient:
            def __init__(self, settings):
                self.settings = settings

            def get_now_playing(self):
                fake.calls.append(self.settings.user)
                if fake.fail:
                    raise RuntimeError("navidrome down")
                return list(fake.entries)

        return FakeClient


def _install_people_fakes(monkeypatch, fake: _NowPlayingFake) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr(presence_module, "_NOW_PLAYING_CACHE", NowPlayingCache(5.0, clock=clock))
    monkeypatch.setattr(presence_module, "NavidromeClient", fake.factory())
    return clock


def _seed_people(store: Store) -> None:
    # Distinct last logins → a deterministic secondary sort order.
    store.upsert_user("Bob", now="2026-01-01T00:00:00+00:00")
    store.upsert_user("carol", now="2026-01-04T00:00:00+00:00")
    store.upsert_user("dave", now="2026-01-02T00:00:00+00:00")
    store.upsert_user("erin", now="2026-01-05T00:00:00+00:00")


def test_people_maps_now_playing_and_sorts(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch)
    _seed_people(store)
    mapped_id = _track(store, tmp_path, "Our Title", "Our Artist")
    store.upsert_external_track("navidrome", "nav-mapped", mapped_id)
    fake = _NowPlayingFake(
        [
            # Caller's own player — must not appear at all.
            NowPlayingEntry(id="nav-mapped", username="alice", state="playing", minutes_ago=0),
            # Bob: two players; the most recent (smallest minutesAgo) wins.
            NowPlayingEntry(id="nav-unmapped", username="bob", title="Old", artist="Stale",
                            state="playing", minutes_ago=5),
            NowPlayingEntry(id="nav-mapped", username="BOB", title="Navidrome Title",
                            artist="Navidrome Artist", state="starting", minutes_ago=1),
            # Carol paused → not "now playing".
            NowPlayingEntry(id="nav-mapped", username="carol", state="paused", minutes_ago=0),
            # Dave: an old client without the state field, unmapped song.
            NowPlayingEntry(id="nav-unmapped", username="Dave", title="Remote Song",
                            artist="Remote Artist", minutes_ago=0),
        ]
    )
    _install_people_fakes(monkeypatch, fake)
    alice = _login("alice")

    response = alice.get("/api/v1/social/people")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["username"] for item in items] == ["dave", "Bob", "erin", "carol"]
    by_name = {item["username"]: item for item in items}
    assert by_name["Bob"]["now_playing"] == {
        "track_id": mapped_id,
        "title": "Our Title",
        "artists": "Our Artist",
        "state": "starting",
    }
    assert by_name["dave"]["now_playing"] == {
        "track_id": None,
        "title": "Remote Song",
        "artists": "Remote Artist",
        "state": "playing",
    }
    assert by_name["carol"]["now_playing"] is None
    assert by_name["erin"]["now_playing"] is None
    for item in items:
        assert set(item) == {"username", "avatar", "now_playing"}
        assert item["avatar"] in AVATAR_KEYS
    # Live data is read with the service account, not the caller's session.
    assert fake.calls == ["svc"]


def test_people_survives_navidrome_outage(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch)
    _seed_people(store)
    _install_people_fakes(monkeypatch, _NowPlayingFake(fail=True))
    alice = _login("alice")

    response = alice.get("/api/v1/social/people")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["username"] for item in items] == ["erin", "carol", "dave", "Bob"]
    assert all(item["now_playing"] is None for item in items)


def test_people_without_service_account_does_not_call_navidrome(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch, service_account=False)
    _seed_people(store)
    fake = _NowPlayingFake([NowPlayingEntry(id="x", username="bob")])
    _install_people_fakes(monkeypatch, fake)
    alice = _login("alice")

    response = alice.get("/api/v1/social/people")

    assert response.status_code == 200
    assert all(item["now_playing"] is None for item in response.json()["items"])
    assert fake.calls == []


def test_people_caches_now_playing_within_ttl(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch)
    _seed_people(store)
    fake = _NowPlayingFake([NowPlayingEntry(id="nav-x", username="bob", title="T", artist="A")])
    clock = _install_people_fakes(monkeypatch, fake)
    alice = _login("alice")
    bob = _login("bob")

    alice.get("/api/v1/social/people")
    clock.now += 4.0
    cached = bob.get("/api/v1/social/people")
    assert len(fake.calls) == 1
    # Bob is the caller here, so his own entry is gone — the cache is shared,
    # the viewer filter is not.
    assert "bob" not in {item["username"].lower() for item in cached.json()["items"]}

    clock.now += 2.0
    alice.get("/api/v1/social/people")
    assert len(fake.calls) == 2


def test_people_caches_failures_within_ttl(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch)
    _seed_people(store)
    fake = _NowPlayingFake(fail=True)
    _install_people_fakes(monkeypatch, fake)
    alice = _login("alice")

    alice.get("/api/v1/social/people")
    alice.get("/api/v1/social/people")

    assert len(fake.calls) == 1


def test_people_requires_a_user(tmp_path: Path, monkeypatch):
    _init(tmp_path, monkeypatch)
    _install_people_fakes(monkeypatch, _NowPlayingFake())
    client = TestClient(app)

    assert client.get(
        "/api/v1/social/people", headers={"X-Discocs-Service-Token": "svc-secret"}
    ).status_code == 403
    assert client.get("/api/v1/social/people").status_code == 401
