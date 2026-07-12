"""Phase 2 multiuser — owner back-fill migration (plans/multiuser-spec.md §2).

Covers the safeguards the spec calls out: existing personal rows are all
reassigned to the owner, a missing/unresolvable owner fails loudly instead of
leaving user_id NULL, the migration is idempotent, and a backup is taken before
touching live data.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.store import INITIALIZED_DB_PATHS, Store
from app.store.base import OWNER_BACKFILL_TABLES, OWNER_USER_ENV
from app.models import utc_now


def init_store(tmp_path: Path, monkeypatch) -> Store:
    db_path = tmp_path / "app.db"
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    monkeypatch.setenv("DISCOCS_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.delenv(OWNER_USER_ENV, raising=False)
    store = Store(db_path)
    store.init()
    return store


def _seed_unscoped_rows(store: Store) -> int:
    """Insert one user_id-NULL row into each back-fill table. Returns the count."""
    now = utc_now()
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO playback_sessions (id, source_type, mode, status, "
            "started_at, updated_at) VALUES ('s1', 'library', 'linear', 'active', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO playback_events (id, event_type, created_at) "
            "VALUES ('e1', 'play', ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO flow_profiles (id, model_key, created_at, updated_at) "
            "VALUES ('f1', 'model-x', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO generated_mixes (id, title, mix_type, status, created_at, "
            "updated_at) VALUES ('m1', 'Mix', 'daily', 'ready', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO playlists (title, kind, created_at, updated_at) "
            "VALUES ('P1', 'manual', ?, ?)",
            (now, now),
        )
    return len(OWNER_BACKFILL_TABLES)


def _count_null_user_id(store: Store) -> int:
    with store.connect() as conn:
        return store._count_unscoped_personal_rows(conn)


def test_fresh_db_needs_no_owner(tmp_path, monkeypatch):
    # Empty DB: init runs the migration and it is a no-op, so no owner env is
    # required and nothing raises.
    store = init_store(tmp_path, monkeypatch)
    assert _count_null_user_id(store) == 0
    # Re-running the migration explicitly is still a no-op.
    store._migrate_owner_backfill()


def test_backfill_assigns_all_rows_to_owner(tmp_path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    seeded = _seed_unscoped_rows(store)
    assert _count_null_user_id(store) == seeded

    monkeypatch.setenv(OWNER_USER_ENV, "infected2202")
    store._migrate_owner_backfill()

    # Owner user row created and every personal row now points at it.
    owner = store.get_user_by_username("infected2202")
    assert owner is not None
    assert _count_null_user_id(store) == 0
    with store.connect() as conn:
        for table in OWNER_BACKFILL_TABLES:
            rows = conn.execute(
                f"SELECT user_id FROM {table}"
            ).fetchall()
            assert rows, f"{table} lost rows"
            assert all(r["user_id"] == owner["id"] for r in rows), table


def test_backfill_without_owner_raises(tmp_path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    _seed_unscoped_rows(store)
    # Unscoped rows present but no owner env → loud failure, not silent NULLs.
    monkeypatch.delenv(OWNER_USER_ENV, raising=False)
    with pytest.raises(RuntimeError, match=OWNER_USER_ENV):
        store._migrate_owner_backfill()
    # Nothing was reassigned.
    assert _count_null_user_id(store) == len(OWNER_BACKFILL_TABLES)


def test_backfill_is_idempotent(tmp_path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    _seed_unscoped_rows(store)
    monkeypatch.setenv(OWNER_USER_ENV, "infected2202")
    store._migrate_owner_backfill()
    owner_id = store.get_user_by_username("infected2202")["id"]

    # A second run finds no NULL rows: no-op, no duplicate owner, no error even
    # if the env were now removed.
    monkeypatch.delenv(OWNER_USER_ENV, raising=False)
    store._migrate_owner_backfill()
    assert _count_null_user_id(store) == 0
    assert store.get_user_by_username("infected2202")["id"] == owner_id


def test_backfill_takes_backup(tmp_path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    _seed_unscoped_rows(store)
    monkeypatch.setenv(OWNER_USER_ENV, "infected2202")
    store._migrate_owner_backfill()

    backups = list(tmp_path.glob("app.db.premigrate-owner-*.bak"))
    assert len(backups) == 1, backups
    assert backups[0].stat().st_size > 0


def test_init_fails_loudly_when_owner_missing(tmp_path, monkeypatch):
    # Simulate an upgrade: a DB with pre-existing personal rows re-initialised
    # without DISCOCS_OWNER_USER must refuse to start.
    store = init_store(tmp_path, monkeypatch)
    _seed_unscoped_rows(store)

    db_path = tmp_path / "app.db"
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    monkeypatch.delenv(OWNER_USER_ENV, raising=False)
    with pytest.raises(RuntimeError, match=OWNER_USER_ENV):
        Store(db_path).init()
