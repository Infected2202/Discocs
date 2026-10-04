"""Startup merge of case-variant duplicate users (app/store/user_merge.py)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.store import INITIALIZED_DB_PATHS, USERNAME_NOCASE_INDEX, Store, merge_case_variant_users

T0 = "2026-07-13T15:10:00+00:00"


def _store(tmp_path: Path) -> Store:
    db_path = tmp_path / "app.db"
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    store = Store(db_path, user_id=None)
    store.init()
    return store


def _legacy_duplicate(store: Store) -> tuple[int, int]:
    """A canonical "carol" plus a later "Carol" row, as old logins created them."""
    canonical = store.upsert_user("carol", now=T0)
    with store.connect() as conn:
        conn.execute(f"DROP INDEX {USERNAME_NOCASE_INDEX}")
        duplicate = int(
            conn.execute(
                "INSERT INTO users (navidrome_username, created_at, last_login_at) VALUES ('Carol', ?, ?)",
                (T0, T0),
            ).lastrowid
        )
    return canonical, duplicate


def _seed(store: Store, user_id: int, *, tag: str, track_pref: dict[int, int], avatar: str) -> None:
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO playback_sessions (id, source_type, mode, status, started_at, updated_at, user_id) "
            "VALUES (?, 'release', 'normal', 'ended', ?, ?, ?)",
            (f"session-{tag}", T0, T0, user_id),
        )
        conn.execute(
            "INSERT INTO playback_events (id, session_id, track_id, event_type, created_at, user_id) "
            "VALUES (?, ?, 1, 'completed', ?, ?)",
            (f"event-{tag}", f"session-{tag}", T0, user_id),
        )
        conn.execute(
            "INSERT INTO listens (user_id, track_id, listened_at, event_id) VALUES (?, 1, ?, ?)",
            (user_id, T0, f"event-{tag}"),
        )
        conn.execute(
            "INSERT INTO generated_mixes (id, title, mix_type, status, created_at, updated_at, user_id) "
            "VALUES (?, 'Mix', 'region', 'active', ?, ?, ?)",
            (f"mix-{tag}", T0, T0, user_id),
        )
        conn.execute(
            "INSERT INTO flow_profiles (id, status, model_key, created_at, updated_at, user_id) "
            "VALUES (?, 'ready', 'discogs_multi', ?, ?, ?)",
            (f"flow-{tag}", T0, T0, user_id),
        )
        for track_id, plays in track_pref.items():
            conn.execute(
                "INSERT INTO user_track_preferences (user_id, track_id, play_count, updated_at) VALUES (?, ?, ?, ?)",
                (user_id, track_id, plays, T0),
            )
        conn.execute(
            "INSERT INTO user_settings (user_id, key, value, updated_at) VALUES (?, 'avatar', ?, ?)",
            (user_id, avatar, T0),
        )


def _rows(store: Store, sql: str, *params: object) -> list[tuple]:
    with store.connect() as conn:
        return [tuple(row) for row in conn.execute(sql, params).fetchall()]


def test_merge_folds_the_duplicate_into_the_canonical_user(tmp_path: Path):
    store = _store(tmp_path)
    canonical, duplicate = _legacy_duplicate(store)
    # Track 10: both have it (canonical is ahead); track 20: only the duplicate.
    _seed(store, canonical, tag="main", track_pref={10: 5}, avatar="a04")
    _seed(store, duplicate, tag="dup", track_pref={10: 4, 20: 1}, avatar="a05")

    merged = merge_case_variant_users(store.db_path)

    assert merged == [(canonical, duplicate)]
    assert _rows(store, "SELECT id, navidrome_username FROM users") == [(canonical, "carol")]
    # History moves over.
    assert _rows(store, "SELECT id, user_id FROM playback_sessions ORDER BY id") == [
        ("session-dup", canonical), ("session-main", canonical)
    ]
    assert _rows(store, "SELECT id, user_id FROM playback_events ORDER BY id") == [
        ("event-dup", canonical), ("event-main", canonical)
    ]
    assert _rows(store, "SELECT event_id, user_id FROM listens ORDER BY event_id") == [
        ("event-dup", canonical), ("event-main", canonical)
    ]
    # Moved mixes are retired so they don't join the live "Mixes For You".
    assert _rows(store, "SELECT id, user_id, status FROM generated_mixes ORDER BY id") == [
        ("mix-dup", canonical, "stale"), ("mix-main", canonical, "active")
    ]
    # Canonical wins on conflicts; what it lacks is moved.
    assert _rows(store, "SELECT user_id, track_id, play_count FROM user_track_preferences ORDER BY track_id") == [
        (canonical, 10, 5), (canonical, 20, 1)
    ]
    assert _rows(store, "SELECT user_id, value FROM user_settings WHERE key = 'avatar'") == [(canonical, "a04")]
    assert _rows(store, "SELECT id, user_id FROM flow_profiles") == [("flow-main", canonical)]


def test_merge_writes_a_backup_first_and_only_when_needed(tmp_path: Path):
    store = _store(tmp_path)
    assert merge_case_variant_users(store.db_path) == []
    assert list(tmp_path.glob("app.db.pre-user-merge-*.bak")) == []

    canonical, duplicate = _legacy_duplicate(store)
    merge_case_variant_users(store.db_path)

    backups = list(tmp_path.glob("app.db.pre-user-merge-*.bak"))
    assert len(backups) == 1
    backup = sqlite3.connect(backups[0])
    try:
        # The backup is the pre-merge state.
        assert backup.execute("SELECT id FROM users ORDER BY id").fetchall() == [(canonical,), (duplicate,)]
    finally:
        backup.close()


def test_merge_adds_a_nocase_unique_index_and_is_idempotent(tmp_path: Path):
    store = _store(tmp_path)
    _legacy_duplicate(store)
    merge_case_variant_users(store.db_path)

    assert merge_case_variant_users(store.db_path) == []
    with store.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO users (navidrome_username, created_at, last_login_at) VALUES ('CAROL', ?, ?)",
            (T0, T0),
        )


def test_merge_aborts_without_a_rule_for_a_referencing_table(tmp_path: Path):
    store = _store(tmp_path)
    canonical, duplicate = _legacy_duplicate(store)
    with store.connect() as conn:
        conn.execute("CREATE TABLE future_feature (user_id INTEGER, note TEXT)")
        conn.execute("INSERT INTO future_feature VALUES (?, 'keep me')", (duplicate,))
        conn.execute(
            "INSERT INTO playback_events (id, track_id, event_type, created_at, user_id) "
            "VALUES ('event-dup', 1, 'completed', ?, ?)",
            (T0, duplicate),
        )

    with pytest.raises(RuntimeError, match="future_feature.user_id"):
        merge_case_variant_users(store.db_path)

    # Rolled back as a whole: nothing moved, nothing deleted.
    assert _rows(store, "SELECT id FROM users ORDER BY id") == [(canonical,), (duplicate,)]
    assert _rows(store, "SELECT user_id FROM playback_events") == [(duplicate,)]


def test_store_init_runs_the_merge(tmp_path: Path):
    store = _store(tmp_path)
    canonical, _duplicate = _legacy_duplicate(store)
    INITIALIZED_DB_PATHS.discard(store.db_path.resolve())

    Store(store.db_path, user_id=None).init()

    assert _rows(store, "SELECT id FROM users") == [(canonical,)]
