"""Listens: one predicate for scrobble + listens, live recording, backfill, isolation."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.serializers.playback as serializers_playback_module
from app.main import app
from app.models import utc_now
from app.scanner import ScannedTrack
from app.store import (
    INITIALIZED_DB_PATHS,
    PlaybackEvent,
    PlaybackEventCreate,
    Store,
    backfill_listens_from_events,
    playback_event_is_listen,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "app.db")
    store.init()
    return store


def _track(store: Store, tmp_path: Path, name: str) -> int:
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=(tmp_path / f"{name}.flac").resolve(),
            artist="Listener",
            title=name,
            album="Listens",
            duration=200.0,
            file_size=1,
            mtime=1,
        )
    )
    return track_id


def _record(store: Store, event_type: str, **kwargs):
    return store.record_playback_event(PlaybackEventCreate(event_type=event_type, **kwargs))


def _event(
    event_id: str,
    event_type: str,
    *,
    session_id: str | None = "s1",
    queue_item_id: str | None = "q1",
    track_id: int | None = 1,
    play_fraction: float | None = None,
) -> PlaybackEvent:
    return PlaybackEvent(
        id=event_id,
        session_id=session_id,
        queue_item_id=queue_item_id,
        track_id=track_id,
        release_id=None,
        artist_id=None,
        event_type=event_type,
        position_seconds=None,
        duration_seconds=None,
        play_fraction=play_fraction,
        created_at="2026-10-01T00:00:00+00:00",
        client_event_id=None,
        source="web",
        payload_json=None,
    )


def _listen_rows(store: Store) -> set[tuple[int, int, str, str]]:
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT user_id, track_id, listened_at, event_id FROM listens"
        ).fetchall()
    return {
        (int(row["user_id"]), int(row["track_id"]), str(row["listened_at"]), str(row["event_id"]))
        for row in rows
    }


# ---------------------------------------------------------------------------
# The predicate
# ---------------------------------------------------------------------------

def test_predicate_threshold_counts_even_after_prior_threshold():
    prior = _event("e1", "play_threshold_reached")

    assert playback_event_is_listen(_event("e2", "play_threshold_reached"), [prior]) is True


def test_predicate_completed_without_prior_counts_and_after_threshold_does_not():
    completed = _event("e2", "completed", play_fraction=1.0)

    assert playback_event_is_listen(completed, []) is True
    assert playback_event_is_listen(completed, [_event("e1", "play_threshold_reached")]) is False
    # A prior completed blocks too; unrelated event types are ignored.
    assert playback_event_is_listen(completed, [_event("e1", "completed", play_fraction=1.0)]) is False
    assert playback_event_is_listen(completed, [_event("e1", "track_started"), _event("e0", "progress")]) is True


def test_predicate_prior_must_match_queue_item_or_track_without_queue():
    other_item = _event("e1", "play_threshold_reached", queue_item_id="q2")
    assert playback_event_is_listen(_event("e2", "completed", play_fraction=1.0), [other_item]) is True

    no_queue = _event("e2", "completed", queue_item_id=None, track_id=7, play_fraction=1.0)
    assert playback_event_is_listen(
        no_queue, [_event("e1", "play_threshold_reached", queue_item_id=None, track_id=7)]
    ) is False
    assert playback_event_is_listen(
        no_queue, [_event("e1", "play_threshold_reached", queue_item_id=None, track_id=8)]
    ) is True


def test_predicate_rejects_non_listen_events():
    assert playback_event_is_listen(_event("e1", "completed", play_fraction=0.5), []) is False
    assert playback_event_is_listen(_event("e1", "play_threshold_reached", track_id=None), []) is False
    for event_type in ("track_started", "progress", "skipped", "liked", "replayed"):
        assert playback_event_is_listen(_event("e1", event_type, play_fraction=1.0), []) is False


def test_predicate_sessionless_completed_ignores_history():
    completed = _event("e2", "completed", session_id=None, queue_item_id=None, play_fraction=1.0)
    prior = _event("e1", "play_threshold_reached", session_id=None, queue_item_id=None)

    assert playback_event_is_listen(completed, [prior]) is True


# ---------------------------------------------------------------------------
# Live recording
# ---------------------------------------------------------------------------

def test_threshold_event_records_listen(tmp_path: Path):
    store = _store(tmp_path)
    track_id = _track(store, tmp_path, "threshold")

    result = _record(store, "play_threshold_reached", track_id=track_id, client_event_id="t-1")

    assert result.listen is True
    listens = store.list_listens()
    assert [(listen.track_id, listen.event_id) for listen in listens] == [(track_id, result.event.id)]
    assert listens[0].listened_at == result.event.created_at


def test_completed_without_threshold_records_listen(tmp_path: Path):
    store = _store(tmp_path)
    track_id = _track(store, tmp_path, "completed")
    session, queue = store.create_playback_session(source_type="manual", track_ids=[track_id])

    result = _record(
        store,
        "completed",
        session_id=session.id,
        queue_item_id=queue[0].id,
        position_seconds=200.0,
        duration_seconds=200.0,
    )

    assert result.listen is True
    assert store.count_listens() == 1


def test_completed_after_threshold_on_same_queue_item_is_not_a_second_listen(tmp_path: Path):
    store = _store(tmp_path)
    track_id = _track(store, tmp_path, "once")
    session, queue = store.create_playback_session(
        source_type="manual", track_ids=[track_id, track_id]
    )

    threshold = _record(store, "play_threshold_reached", session_id=session.id, queue_item_id=queue[0].id)
    completed = _record(
        store, "completed", session_id=session.id, queue_item_id=queue[0].id, play_fraction=1.0
    )
    # Same track queued again is a separate play.
    replay = _record(
        store, "completed", session_id=session.id, queue_item_id=queue[1].id, play_fraction=1.0
    )

    assert (threshold.listen, completed.listen, replay.listen) == (True, False, True)
    assert {listen.event_id for listen in store.list_listens()} == {
        threshold.event.id,
        replay.event.id,
    }


def test_duplicate_and_non_qualifying_events_record_nothing(tmp_path: Path):
    store = _store(tmp_path)
    track_id = _track(store, tmp_path, "noise")

    first = _record(store, "play_threshold_reached", track_id=track_id, client_event_id="dup-1")
    duplicate = _record(store, "play_threshold_reached", track_id=track_id, client_event_id="dup-1")
    low = _record(store, "completed", track_id=track_id, play_fraction=0.5)
    started = _record(store, "track_started", track_id=track_id)
    skipped = _record(store, "skipped", track_id=track_id, position_seconds=5.0, duration_seconds=200.0)

    assert first.listen is True
    assert duplicate.duplicate is True and duplicate.listen is False
    assert (low.listen, started.listen, skipped.listen) == (False, False, False)
    assert store.count_listens() == 1


def test_listens_are_isolated_per_user(tmp_path: Path):
    base = _store(tmp_path)
    track_id = _track(base, tmp_path, "shared")
    alice = base.for_user(base.upsert_user("alice", now=utc_now()))
    bob = base.for_user(base.upsert_user("bob", now=utc_now()))

    alice_result = _record(alice, "play_threshold_reached", track_id=track_id)
    _record(alice, "play_threshold_reached", track_id=track_id)
    bob_result = _record(bob, "play_threshold_reached", track_id=track_id)

    assert alice.count_listens() == 2
    assert bob.count_listens() == 1
    assert [listen.event_id for listen in bob.list_listens()] == [bob_result.event.id]
    assert alice_result.event.id in {listen.event_id for listen in alice.list_listens()}
    assert {listen.user_id for listen in alice.list_listens()} == {alice.user_id}
    with pytest.raises(PermissionError):
        Store(tmp_path / "app.db", user_id=None).list_listens()


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

def _record_mixed_history(base: Store, tmp_path: Path) -> tuple[Store, Store]:
    first = _track(base, tmp_path, "first")
    second = _track(base, tmp_path, "second")
    alice = base.for_user(base.upsert_user("alice", now=utc_now()))
    bob = base.for_user(base.upsert_user("bob", now=utc_now()))

    session, queue = alice.create_playback_session(
        source_type="manual", track_ids=[first, second, first]
    )
    _record(alice, "track_started", session_id=session.id, queue_item_id=queue[0].id)
    _record(alice, "play_threshold_reached", session_id=session.id, queue_item_id=queue[0].id, client_event_id="a-t1")
    _record(alice, "play_threshold_reached", session_id=session.id, queue_item_id=queue[0].id, client_event_id="a-t1")
    _record(alice, "completed", session_id=session.id, queue_item_id=queue[0].id, play_fraction=1.0)
    _record(alice, "completed", session_id=session.id, queue_item_id=queue[1].id, play_fraction=0.4)
    _record(alice, "completed", session_id=session.id, queue_item_id=queue[2].id, play_fraction=1.0)
    _record(alice, "completed", track_id=second, play_fraction=1.0)

    bob_session, bob_queue = bob.create_playback_session(source_type="manual", track_ids=[second])
    _record(bob, "completed", session_id=bob_session.id, queue_item_id=bob_queue[0].id, play_fraction=1.0)
    _record(bob, "play_threshold_reached", session_id=bob_session.id, queue_item_id=bob_queue[0].id)
    return alice, bob


def test_backfill_reproduces_live_listens_and_is_idempotent(tmp_path: Path):
    base = _store(tmp_path)
    alice, bob = _record_mixed_history(base, tmp_path)
    live = _listen_rows(base)
    # threshold + 3rd queue item + sessionless completed for alice;
    # completed then threshold (threshold always counts) for bob.
    assert alice.count_listens() == 3
    assert bob.count_listens() == 2

    with base.connect() as conn:
        conn.execute("DELETE FROM listens")

    assert base.backfill_listens() == len(live)
    assert _listen_rows(base) == live
    assert base.backfill_listens() == 0
    assert _listen_rows(base) == live


def test_startup_backfills_an_empty_listens_table(tmp_path: Path):
    base = _store(tmp_path)
    _record_mixed_history(base, tmp_path)
    live = _listen_rows(base)
    with base.connect() as conn:
        conn.execute("DELETE FROM listens")

    INITIALIZED_DB_PATHS.discard((tmp_path / "app.db").resolve())
    _store(tmp_path)

    assert _listen_rows(base) == live


def test_backfill_skips_events_without_a_known_user(tmp_path: Path):
    base = _store(tmp_path)
    track_id = _track(base, tmp_path, "orphan")
    with base.connect() as conn:
        for event_id, user_id in (("null-user", None), ("ghost-user", 999_999)):
            conn.execute(
                """
                INSERT INTO playback_events (id, user_id, track_id, event_type, created_at, source)
                VALUES (?, ?, ?, 'play_threshold_reached', ?, 'web')
                """,
                (event_id, user_id, track_id, utc_now()),
            )
        inserted = backfill_listens_from_events(conn)
        listened = conn.execute("SELECT event_id FROM listens").fetchall()

    assert inserted == 0
    assert listened == []


# ---------------------------------------------------------------------------
# API: listens are independent of Navidrome; scrobble follows the same verdict
# ---------------------------------------------------------------------------

def _init_api_store(tmp_path: Path, monkeypatch) -> Store:
    db_path = tmp_path / "app.db"
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    monkeypatch.setenv("DISCOCS_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.delenv("DISCOCS_AUTH_ENABLED", raising=False)
    for name in (
        "DISCOCS_NAVIDROME_URL",
        "DISCOCS_NAVIDROME_USER",
        "DISCOCS_NAVIDROME_PASSWORD",
        "DISCOCS_NAVIDROME_AUTH_MODE",
    ):
        monkeypatch.delenv(name, raising=False)
    store = Store(db_path)
    store.init()
    return store


def test_api_records_listen_without_navidrome(tmp_path: Path, monkeypatch):
    store = _init_api_store(tmp_path, monkeypatch)
    track_id = _track(store, tmp_path, "offline")
    client = TestClient(app)

    response = client.post(
        "/api/v1/playback/events",
        json={"track_id": track_id, "event_type": "play_threshold_reached", "client_event_id": "offline-1"},
    )

    assert response.status_code == 200
    assert response.json()["navidrome_scrobble"]["status"] == "skipped"
    assert [listen.event_id for listen in store.list_listens()] == [response.json()["event_id"]]


def test_api_records_listen_when_scrobble_fails(tmp_path: Path, monkeypatch):
    store = _init_api_store(tmp_path, monkeypatch)
    track_id = _track(store, tmp_path, "failing")
    store.upsert_external_track("navidrome", "nav-fail", track_id)
    monkeypatch.setenv("DISCOCS_NAVIDROME_URL", "http://navidrome.example")
    monkeypatch.setenv("DISCOCS_NAVIDROME_USER", "user")
    monkeypatch.setenv("DISCOCS_NAVIDROME_PASSWORD", "secret")

    class FailingNavidromeClient:
        def __init__(self, _settings):
            pass

        def scrobble_song(self, *_args, **_kwargs):
            raise RuntimeError("navidrome down")

    monkeypatch.setattr(serializers_playback_module, "NavidromeClient", FailingNavidromeClient)
    client = TestClient(app)

    response = client.post(
        "/api/v1/playback/events",
        json={"track_id": track_id, "event_type": "play_threshold_reached"},
    )

    assert response.json()["navidrome_scrobble"]["status"] == "failed"
    assert store.count_listens() == 1


def test_api_scrobbles_exactly_the_recorded_listens(tmp_path: Path, monkeypatch):
    store = _init_api_store(tmp_path, monkeypatch)
    track_id = _track(store, tmp_path, "mapped")
    store.upsert_external_track("navidrome", "nav-mapped", track_id)
    monkeypatch.setenv("DISCOCS_NAVIDROME_URL", "http://navidrome.example")
    monkeypatch.setenv("DISCOCS_NAVIDROME_USER", "user")
    monkeypatch.setenv("DISCOCS_NAVIDROME_PASSWORD", "secret")
    submissions: list[bool] = []

    class FakeNavidromeClient:
        def __init__(self, _settings):
            pass

        def scrobble_song(self, _item_id, *, played_at_ms=None, submission=True):
            submissions.append(submission)
            return {}

    monkeypatch.setattr(serializers_playback_module, "NavidromeClient", FakeNavidromeClient)
    session, queue = store.create_playback_session(source_type="manual", track_ids=[track_id])
    client = TestClient(app)

    def post(event_type: str, **extra) -> dict:
        body = {"session_id": session.id, "queue_item_id": queue[0].id, "event_type": event_type, **extra}
        response = client.post("/api/v1/playback/events", json=body)
        assert response.status_code == 200
        return response.json()["navidrome_scrobble"]

    completed_first = post("completed", play_fraction=1.0)
    threshold_after = post("play_threshold_reached")
    completed_again = post("completed", play_fraction=1.0)

    assert completed_first["mode"] == "submission"
    assert threshold_after["mode"] == "submission"
    assert completed_again == {"status": "skipped", "reason": "event_not_scrobbleable"}
    assert submissions == [True, True]
    assert store.count_listens() == 2
