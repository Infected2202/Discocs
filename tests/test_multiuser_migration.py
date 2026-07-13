"""Current multiuser schema contract after retiring the one-time migration."""
from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from app.store import INITIALIZED_DB_PATHS, Store
from app.models import utc_now


EXPECTED_PRIMARY_KEYS = {
    "user_track_preferences": ("user_id", "track_id"),
    "user_release_preferences": ("user_id", "release_id"),
    "user_artist_preferences": ("user_id", "artist_id"),
    "albums_for_you_cache": ("user_id", "model_name"),
}


def _configure_store(tmp_path: Path, monkeypatch, name: str = "app.db") -> Path:
    db_path = tmp_path / name
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    monkeypatch.setenv("DISCOCS_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_MODEL_DIR", str(tmp_path / "models"))
    return db_path


def test_fresh_database_uses_current_multiuser_primary_keys(tmp_path, monkeypatch):
    db_path = _configure_store(tmp_path, monkeypatch)
    store = Store(db_path)
    store.init()

    with store.connect() as conn:
        for table, expected in EXPECTED_PRIMARY_KEYS.items():
            columns = conn.execute(f"PRAGMA table_info({table})").fetchall()
            actual = tuple(
                row["name"]
                for row in sorted(columns, key=lambda row: int(row["pk"]))
                if int(row["pk"]) > 0
            )
            assert actual == expected


def test_legacy_primary_key_fails_loudly_without_mutation(tmp_path, monkeypatch):
    db_path = _configure_store(tmp_path, monkeypatch, "legacy.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE user_track_preferences ("
            "track_id INTEGER PRIMARY KEY, liked INTEGER NOT NULL DEFAULT 0)"
        )

    with pytest.raises(RuntimeError, match="Unsupported legacy database schema"):
        Store(db_path).init()

    assert not list(tmp_path.glob("*.premigrate-*.bak"))


def test_unscoped_personal_rows_fail_loudly_without_backfill(tmp_path, monkeypatch):
    db_path = _configure_store(tmp_path, monkeypatch)
    store = Store(db_path)
    store.init()
    now = utc_now()
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO playback_sessions "
            "(id, source_type, mode, status, started_at, updated_at) "
            "VALUES ('legacy', 'library', 'linear', 'active', ?, ?)",
            (now, now),
        )

    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    with pytest.raises(RuntimeError, match="playback_sessions contains rows without user_id"):
        Store(db_path).init()

    assert not list(tmp_path.glob("*.premigrate-*.bak"))
