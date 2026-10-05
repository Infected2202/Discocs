"""Listen along (social Ф6): presence persisted on the session and the one-shot pick-up."""
from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.services.presence as presence_module
from app import auth
from app.autoplay import build_source_context
from app.main import app
from app.navidrome import NowPlayingEntry
from app.scanner import ScannedTrack
from app.services.listen_along import live_position_ms
from app.services.presence import NowPlayingCache
from app.store import INITIALIZED_DB_PATHS, Store

USERS = {"alice", "bob", "carol"}
TRACK_SECONDS = 200.0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _init(tmp_path: Path, monkeypatch) -> Store:
    db_path = tmp_path / "app.db"
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    monkeypatch.setenv("DISCOCS_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("DISCOCS_AUTH_ENABLED", "true")
    monkeypatch.setenv("DISCOCS_SERVICE_TOKEN", "svc-secret")
    monkeypatch.setenv("DISCOCS_OWNER_USER", "alice")
    monkeypatch.setenv("DISCOCS_NAVIDROME_URL", "http://navidrome:4533")
    monkeypatch.setenv("DISCOCS_NAVIDROME_USER", "svc")
    monkeypatch.setenv("DISCOCS_NAVIDROME_PASSWORD", "svc-pass")
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


def _user_id(store: Store, username: str) -> int:
    row = store.get_user_by_username(username)
    assert row is not None
    return int(row["id"])


def _tracks(store: Store, tmp_path: Path, count: int, *, mapped: bool = True) -> list[int]:
    ids: list[int] = []
    for index in range(count):
        track_id, _changed = store.upsert_track(
            ScannedTrack(
                path=(tmp_path / f"t{index}.flac").resolve(),
                artist=f"Artist {index}",
                title=f"Track {index}",
                album=f"Album {index}",
                duration=TRACK_SECONDS,
                file_size=1,
                mtime=1,
            )
        )
        if mapped:
            store.upsert_external_track("navidrome", f"nav-{track_id}", track_id)
        ids.append(track_id)
    return ids


class _NavidromeFake:
    """Both Navidrome calls presence makes: reportPlayback and getNowPlaying."""

    def __init__(self, entries: list[NowPlayingEntry] | None = None, *, fail: bool = False):
        self.entries = entries or []
        self.fail = fail
        self.reports: list[dict[str, object]] = []

    def factory(self):
        fake = self

        class FakeClient:
            def __init__(self, settings):
                self.settings = settings

            def report_playback(self, media_id, *, state, position_ms, playback_rate=1.0, ignore_scrobble=True):
                if fake.fail:
                    raise RuntimeError("navidrome down")
                fake.reports.append({"media_id": media_id, "state": state})
                return {}

            def get_now_playing(self):
                if fake.fail:
                    raise RuntimeError("navidrome down")
                return list(fake.entries)

        return FakeClient


def _install(monkeypatch, fake: _NavidromeFake) -> None:
    # A fresh cache per test: the module-level one would leak entries between tests.
    monkeypatch.setattr(presence_module, "_NOW_PLAYING_CACHE", NowPlayingCache(5.0))
    monkeypatch.setattr(presence_module, "NavidromeClient", fake.factory())


def _playing(username: str, track_id: int, *, state: str = "playing", position_ms: int | None = None):
    return NowPlayingEntry(
        id=f"nav-{track_id}", username=username, state=state, position_ms=position_ms, minutes_ago=0
    )


def _presence_row(store: Store, session_id: str) -> dict[str, object]:
    with store.connect() as conn:
        row = conn.execute(
            """SELECT presence_state, presence_position_ms, presence_at,
                      presence_track_id, presence_queue_item_id
               FROM playback_sessions WHERE id = ?""",
            (session_id,),
        ).fetchone()
    return dict(row)


def _queue_track_ids(envelope: dict) -> list[int]:
    return [item["track_id"] for item in envelope["queue"]["items"]]


def _iso(moment: datetime) -> str:
    return moment.isoformat()


# ---------------------------------------------------------------------------
# Presence persisted on the session
# ---------------------------------------------------------------------------

def test_presence_with_session_id_is_stored_on_own_session(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch)
    track_ids = _tracks(store, tmp_path, 3)
    # Navidrome being down must not stop the local write.
    _install(monkeypatch, _NavidromeFake(fail=True))
    bob = _login("bob")
    envelope = bob.post(
        "/api/v1/playback/sessions",
        json={"source_type": "manual", "track_ids": track_ids, "source_label": "Mine"},
    ).json()
    session_id = envelope["session"]["id"]
    second_item = envelope["queue"]["items"][1]

    response = bob.post(
        "/api/v1/playback/presence",
        json={
            "track_id": track_ids[1],
            "state": "paused",
            "position_ms": 61_000,
            "session_id": session_id,
            "queue_item_id": second_item["id"],
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    row = _presence_row(store, session_id)
    assert row["presence_state"] == "paused"
    assert row["presence_position_ms"] == 61_000
    assert row["presence_track_id"] == track_ids[1]
    assert row["presence_queue_item_id"] == second_item["id"]
    assert row["presence_at"]
    # The player's queue protocol fields are not touched by presence.
    session = bob.get(f"/api/v1/playback/sessions/{session_id}").json()["session"]
    assert session["current_queue_item_id"] == envelope["queue"]["items"][0]["id"]


def test_presence_without_mapping_still_stored_and_foreign_queue_item_dropped(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch)
    unmapped = _tracks(store, tmp_path, 2, mapped=False)
    _install(monkeypatch, _NavidromeFake())
    bob = _login("bob")
    mine = bob.post(
        "/api/v1/playback/sessions", json={"source_type": "manual", "track_ids": unmapped, "source_label": "x"}
    ).json()
    other = bob.post(
        "/api/v1/playback/sessions", json={"source_type": "manual", "track_ids": unmapped, "source_label": "y"}
    ).json()

    response = bob.post(
        "/api/v1/playback/presence",
        json={
            "track_id": unmapped[0],
            "state": "playing",
            "position_ms": 1000,
            "session_id": mine["session"]["id"],
            # An item of another session is not trusted as this session's current one.
            "queue_item_id": other["queue"]["items"][0]["id"],
        },
    )

    assert response.json()["reason"] == "no_navidrome_mapping"
    row = _presence_row(store, mine["session"]["id"])
    assert row["presence_state"] == "playing"
    assert row["presence_track_id"] == unmapped[0]
    assert row["presence_queue_item_id"] is None


def test_presence_ignores_another_users_session(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch)
    track_ids = _tracks(store, tmp_path, 2)
    _install(monkeypatch, _NavidromeFake())
    bob = _login("bob")
    bob_session = bob.post(
        "/api/v1/playback/sessions", json={"source_type": "manual", "track_ids": track_ids, "source_label": "b"}
    ).json()["session"]["id"]
    alice = _login("alice")

    response = alice.post(
        "/api/v1/playback/presence",
        json={"track_id": track_ids[0], "state": "playing", "position_ms": 5000, "session_id": bob_session},
    )

    assert response.status_code == 200
    assert _presence_row(store, bob_session) == {
        "presence_state": None,
        "presence_position_ms": None,
        "presence_at": None,
        "presence_track_id": None,
        "presence_queue_item_id": None,
    }


def test_store_presence_write_is_scoped_to_bound_user(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch)
    track_ids = _tracks(store, tmp_path, 1)
    bob_id = store.upsert_user("bob", now="2026-01-01T00:00:00+00:00")
    carol_id = store.upsert_user("carol", now="2026-01-01T00:00:00+00:00")
    session, _queue = store.for_user(bob_id).create_playback_session(source_type="manual", track_ids=track_ids)

    assert store.for_user(carol_id).record_playback_presence(
        session.id, track_id=track_ids[0], state="playing", position_ms=1
    ) is False
    assert store.for_user(bob_id).record_playback_presence(
        session.id, track_id=track_ids[0], state="playing", position_ms=1
    ) is True


# ---------------------------------------------------------------------------
# Position math
# ---------------------------------------------------------------------------

NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("state", "position_ms", "seconds_ago", "duration", "expected"),
    [
        ("playing", 30_000, 12, 200.0, 42_000),
        ("starting", 0, 3, 200.0, 3_000),
        ("paused", 30_000, 120, 200.0, 30_000),
        ("stopped", 30_000, 120, 200.0, 30_000),
        # Ran past the end of the track: the report is stale → from the start.
        ("playing", 190_000, 15, 200.0, 0),
        ("paused", 200_000, 0, 200.0, 0),
        # Unknown duration: no cut-off.
        ("playing", 190_000, 15, None, 205_000),
    ],
)
def test_live_position(state, position_ms, seconds_ago, duration, expected):
    assert live_position_ms(
        state=state,
        position_ms=position_ms,
        reported_at=_iso(NOW - timedelta(seconds=seconds_ago)),
        duration_seconds=duration,
        now=NOW,
    ) == expected


def test_live_position_without_report_is_zero():
    assert live_position_ms(state="playing", position_ms=None, reported_at=None, duration_seconds=200.0, now=NOW) == 0
    assert live_position_ms(state="playing", position_ms=5000, reported_at=None, duration_seconds=200.0, now=NOW) == 0


# ---------------------------------------------------------------------------
# POST /users/{username}/listen-along
# ---------------------------------------------------------------------------

def _bob_session(store: Store, track_ids: list[int], **kwargs):
    bob_id = store.upsert_user("bob", now="2026-01-01T00:00:00+00:00")
    bob_store = store.for_user(bob_id)
    session, queue = bob_store.create_playback_session(
        source_type=kwargs.pop("source_type", "manual"), track_ids=track_ids, **kwargs
    )
    return bob_id, bob_store, session, queue


def test_listen_along_copies_remaining_queue_from_current_item(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch)
    track_ids = _tracks(store, tmp_path, 5)
    bob_id, bob_store, session, queue = _bob_session(store, track_ids, source_type="release", source_id=1)
    bob_store.record_playback_presence(
        session.id,
        track_id=track_ids[2],
        state="paused",
        position_ms=30_000,
        queue_item_id=queue[2].id,
    )
    _install(monkeypatch, _NavidromeFake([_playing("bob", track_ids[2])]))
    alice = _login("alice")

    response = alice.post("/api/v1/users/BOB/listen-along")

    assert response.status_code == 200, response.text
    body = response.json()
    assert _queue_track_ids(body) == track_ids[2:]
    assert body["start_track_id"] == track_ids[2]
    assert body["start_queue_item_id"] == body["queue"]["items"][0]["id"]
    assert body["queue"]["current_item"]["track_id"] == track_ids[2]
    assert body["start_position_seconds"] == 30.0
    assert body["listen_along"] == {"host": "bob", "strategy": "queue"}
    new_session = body["session"]
    assert new_session["source_type"] == "listen_along"
    assert new_session["source_id"] == bob_id
    assert new_session["source_label"] == "bob"
    assert new_session["autoplay_enabled"] is True
    assert new_session["shuffle_enabled"] is False
    # The new session is the viewer's own; the host's is untouched.
    assert alice.get(f"/api/v1/playback/sessions/{new_session['id']}").status_code == 200
    assert bob_store.get_playback_session(new_session["id"]) is None
    assert [item.track_id for item in bob_store.list_queue_items(session.id)] == track_ids
    assert bob_store.get_playback_session(session.id).current_queue_item_id == queue[0].id


def test_listen_along_keeps_the_hosts_shuffled_play_order(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch)
    track_ids = _tracks(store, tmp_path, 5)
    # Deterministic "shuffle": reversed source order.
    monkeypatch.setattr(random, "shuffle", lambda items: items.reverse())
    _bob_id, bob_store, session, queue = _bob_session(
        store, track_ids, source_type="manual", shuffle_enabled=True
    )
    play_order = [item.track_id for item in queue]
    assert play_order == list(reversed(track_ids))
    current = queue[1]  # play order index 1 = track_ids[3]
    bob_store.record_playback_presence(
        session.id, track_id=current.track_id, state="playing", position_ms=0, queue_item_id=current.id
    )
    _install(monkeypatch, _NavidromeFake([_playing("bob", current.track_id)]))
    alice = _login("alice")

    body = alice.post("/api/v1/users/bob/listen-along").json()

    # Play order from the current item, not the source order.
    assert _queue_track_ids(body) == play_order[1:]
    assert _queue_track_ids(body) == [track_ids[3], track_ids[2], track_ids[1], track_ids[0]]


def test_listen_along_without_reported_item_uses_session_current_item(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch)
    track_ids = _tracks(store, tmp_path, 4)
    _bob_id, bob_store, session, queue = _bob_session(store, track_ids)
    bob_store.jump_to_queue_item(session.id, queue[1].id)
    _install(monkeypatch, _NavidromeFake([_playing("bob", track_ids[1], position_ms=12_000)]))
    alice = _login("alice")

    body = alice.post("/api/v1/users/bob/listen-along").json()

    assert _queue_track_ids(body) == track_ids[1:]
    # No presence report for this track → Navidrome's position.
    assert body["start_position_seconds"] == 12.0


@pytest.mark.parametrize("source_type", ["flow", "generated_mix"])
def test_listen_along_personal_source_is_track_radio(tmp_path: Path, monkeypatch, source_type):
    store = _init(tmp_path, monkeypatch)
    track_ids = _tracks(store, tmp_path, 4)
    _bob_id, bob_store, session, queue = _bob_session(store, track_ids, source_type=source_type)
    bob_store.record_playback_presence(
        session.id,
        track_id=track_ids[1],
        state="paused",
        position_ms=50_000,
        queue_item_id=queue[1].id,
    )
    _install(monkeypatch, _NavidromeFake([_playing("bob", track_ids[1])]))
    alice = _login("alice")

    body = alice.post("/api/v1/users/bob/listen-along").json()

    assert _queue_track_ids(body) == [track_ids[1]]
    assert body["session"]["mode"] == "radio"
    assert body["session"]["autoplay_enabled"] is True
    assert body["start_position_seconds"] == 50.0
    assert body["listen_along"]["strategy"] == "personal_source"


def test_listen_along_without_host_session_is_track_radio(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch)
    track_ids = _tracks(store, tmp_path, 2)
    store.upsert_user("bob", now="2026-01-01T00:00:00+00:00")
    # Bob plays from another Navidrome client: no discocs session at all.
    _install(monkeypatch, _NavidromeFake([_playing("bob", track_ids[0], position_ms=45_000)]))
    alice = _login("alice")

    body = alice.post("/api/v1/users/bob/listen-along").json()

    assert _queue_track_ids(body) == [track_ids[0]]
    assert body["session"]["source_type"] == "listen_along"
    assert body["start_position_seconds"] == 45.0
    assert body["listen_along"]["strategy"] == "no_session"


def test_listen_along_no_session_position_past_duration_is_zero(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch)
    track_ids = _tracks(store, tmp_path, 1)
    store.upsert_user("bob", now="2026-01-01T00:00:00+00:00")
    _install(monkeypatch, _NavidromeFake([_playing("bob", track_ids[0], position_ms=250_000)]))
    alice = _login("alice")

    body = alice.post("/api/v1/users/bob/listen-along").json()

    assert body["start_position_seconds"] == 0.0


def test_listen_along_position_advances_while_host_plays(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch)
    track_ids = _tracks(store, tmp_path, 3)
    _bob_id, bob_store, session, queue = _bob_session(store, track_ids)
    reported_at = datetime.now(UTC) - timedelta(seconds=10)
    bob_store.record_playback_presence(
        session.id,
        track_id=track_ids[0],
        state="playing",
        position_ms=30_000,
        queue_item_id=queue[0].id,
        at=_iso(reported_at),
    )
    _install(monkeypatch, _NavidromeFake([_playing("bob", track_ids[0])]))
    alice = _login("alice")

    position = alice.post("/api/v1/users/bob/listen-along").json()["start_position_seconds"]

    assert 40.0 <= position < 45.0


def test_listen_along_stale_playing_report_starts_from_zero(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch)
    track_ids = _tracks(store, tmp_path, 2)
    _bob_id, bob_store, session, queue = _bob_session(store, track_ids)
    bob_store.record_playback_presence(
        session.id,
        track_id=track_ids[0],
        state="playing",
        position_ms=150_000,
        queue_item_id=queue[0].id,
        at=_iso(datetime.now(UTC) - timedelta(minutes=5)),
    )
    _install(monkeypatch, _NavidromeFake([_playing("bob", track_ids[0])]))
    alice = _login("alice")

    body = alice.post("/api/v1/users/bob/listen-along").json()

    assert body["start_position_seconds"] == 0.0
    assert _queue_track_ids(body) == track_ids


def test_listen_along_picks_the_freshest_presence(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch)
    track_ids = _tracks(store, tmp_path, 4)
    bob_id = store.upsert_user("bob", now="2026-01-01T00:00:00+00:00")
    bob_store = store.for_user(bob_id)
    old, old_queue = bob_store.create_playback_session(
        source_type="manual", track_ids=[track_ids[0], track_ids[1]]
    )
    fresh, fresh_queue = bob_store.create_playback_session(
        source_type="manual", track_ids=[track_ids[0], track_ids[2], track_ids[3]]
    )
    now = datetime.now(UTC)
    bob_store.record_playback_presence(
        fresh.id, track_id=track_ids[0], state="paused", position_ms=7_000,
        queue_item_id=fresh_queue[0].id, at=_iso(now - timedelta(minutes=1)),
    )
    # Written later, but reported earlier: presence_at decides, not write order.
    bob_store.record_playback_presence(
        old.id, track_id=track_ids[0], state="paused", position_ms=3_000,
        queue_item_id=old_queue[0].id, at=_iso(now - timedelta(hours=1)),
    )
    _install(monkeypatch, _NavidromeFake([_playing("bob", track_ids[0])]))
    alice = _login("alice")

    body = alice.post("/api/v1/users/bob/listen-along").json()

    assert _queue_track_ids(body) == [track_ids[0], track_ids[2], track_ids[3]]
    assert body["start_position_seconds"] == 7.0


def test_listen_along_on_self_is_400(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch)
    track_ids = _tracks(store, tmp_path, 1)
    _install(monkeypatch, _NavidromeFake([_playing("alice", track_ids[0])]))
    alice = _login("alice")

    response = alice.post("/api/v1/users/Alice/listen-along")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert _count_listen_along_sessions(store) == 0


def _count_listen_along_sessions(store: Store) -> int:
    with store.connect() as conn:
        return int(
            conn.execute("SELECT COUNT(*) FROM playback_sessions WHERE source_type = 'listen_along'").fetchone()[0]
        )


@pytest.mark.parametrize(
    "entries",
    [
        [],
        [NowPlayingEntry(id="nav-x", username="bob", state="paused", minutes_ago=0)],
        [NowPlayingEntry(id="nav-x", username="carol", state="playing", minutes_ago=0)],
    ],
)
def test_listen_along_host_not_playing_is_409(tmp_path: Path, monkeypatch, entries):
    store = _init(tmp_path, monkeypatch)
    store.upsert_user("bob", now="2026-01-01T00:00:00+00:00")
    _install(monkeypatch, _NavidromeFake(entries))
    alice = _login("alice")

    response = alice.post("/api/v1/users/bob/listen-along")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_playing"
    assert _count_listen_along_sessions(store) == 0


def test_listen_along_navidrome_down_is_409(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch)
    store.upsert_user("bob", now="2026-01-01T00:00:00+00:00")
    _install(monkeypatch, _NavidromeFake(fail=True))
    alice = _login("alice")

    response = alice.post("/api/v1/users/bob/listen-along")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_playing"


def test_listen_along_unmapped_track_is_409(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch)
    store.upsert_user("bob", now="2026-01-01T00:00:00+00:00")
    _install(
        monkeypatch,
        _NavidromeFake([NowPlayingEntry(id="nav-unknown", username="bob", state="playing", minutes_ago=0)]),
    )
    alice = _login("alice")

    response = alice.post("/api/v1/users/bob/listen-along")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "track_not_mapped"
    assert _count_listen_along_sessions(store) == 0


def test_listen_along_unknown_user_and_service_principal(tmp_path: Path, monkeypatch):
    _init(tmp_path, monkeypatch)
    _install(monkeypatch, _NavidromeFake())
    alice = _login("alice")

    assert alice.post("/api/v1/users/nobody/listen-along").status_code == 404
    service = TestClient(app).post(
        "/api/v1/users/alice/listen-along", headers={"X-Discocs-Service-Token": "svc-secret"}
    )
    assert service.status_code == 403
    assert TestClient(app).post("/api/v1/users/alice/listen-along").status_code == 401


def test_listen_along_session_seeds_autoplay_from_its_queue(tmp_path: Path, monkeypatch):
    store = _init(tmp_path, monkeypatch)
    track_ids = _tracks(store, tmp_path, 3)
    bob_id, bob_store, session, queue = _bob_session(store, track_ids)
    bob_store.record_playback_presence(
        session.id, track_id=track_ids[1], state="paused", position_ms=0, queue_item_id=queue[1].id
    )
    _install(monkeypatch, _NavidromeFake([_playing("bob", track_ids[1])]))
    alice = _login("alice")
    body = alice.post("/api/v1/users/bob/listen-along").json()

    alice_store = store.for_user(_user_id(store, "alice"))
    new_session = alice_store.get_playback_session(body["session"]["id"])
    context = build_source_context(alice_store, new_session)

    # source_id is bob's *user* id — it must never be read as a seed track.
    assert new_session.source_id == bob_id
    assert context.source_track_ids == track_ids[1:]
    assert context.source_debug["strategy"] == "listen_along_queue"
