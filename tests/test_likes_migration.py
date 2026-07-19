"""Schema contract for `liked_at`.

The one-shot cleanup of inherited release/artist likes was retired after
production migrated; the starred sync now replaces all three like sets, so even
an old polluted backup is corrected by the first sync. See
plans/likes-unification-plan.md.
"""
from __future__ import annotations

from pathlib import Path

from app.models import utc_now
from app.store import INITIALIZED_DB_PATHS, Store


def _configure_store(tmp_path: Path, monkeypatch, name: str = "app.db") -> Path:
    db_path = tmp_path / name
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    monkeypatch.setenv("DISCOCS_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_MODEL_DIR", str(tmp_path / "models"))
    return db_path


def _reinit(db_path: Path) -> Store:
    """Re-run init() on an existing database, bypassing the process-wide cache."""
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    store = Store(db_path)
    store.init()
    return store


PREFERENCE_TABLES = (
    "user_track_preferences",
    "user_release_preferences",
    "user_artist_preferences",
)


def test_liked_at_column_exists_on_all_preference_tables(tmp_path, monkeypatch):
    db_path = _configure_store(tmp_path, monkeypatch)
    store = Store(db_path)
    store.init()

    with store.connect() as conn:
        for table in PREFERENCE_TABLES:
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            assert "liked_at" in columns, table


def test_liked_at_backfilled_from_updated_at_for_existing_likes(tmp_path, monkeypatch):
    db_path = _configure_store(tmp_path, monkeypatch)
    store = Store(db_path)
    store.init()
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO user_track_preferences (user_id, track_id, liked, updated_at) "
            "VALUES (?, 1, 1, ?)",
            (store.user_id, utc_now()),
        )

    migrated = _reinit(db_path)

    with migrated.connect() as conn:
        row = conn.execute(
            "SELECT liked_at, updated_at FROM user_track_preferences WHERE track_id = 1"
        ).fetchone()
    assert row["liked_at"] == row["updated_at"]


def test_backfill_does_not_touch_unliked_rows(tmp_path, monkeypatch):
    db_path = _configure_store(tmp_path, monkeypatch)
    store = Store(db_path)
    store.init()
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO user_track_preferences (user_id, track_id, liked, updated_at) "
            "VALUES (?, 2, 0, ?)",
            (store.user_id, utc_now()),
        )

    migrated = _reinit(db_path)

    with migrated.connect() as conn:
        liked_at = conn.execute(
            "SELECT liked_at FROM user_track_preferences WHERE track_id = 2"
        ).fetchone()["liked_at"]
    assert liked_at is None


def test_backfill_preserves_an_explicit_liked_at(tmp_path, monkeypatch):
    db_path = _configure_store(tmp_path, monkeypatch)
    store = Store(db_path)
    store.init()
    explicit = "2020-01-01T00:00:00+00:00"
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO user_track_preferences (user_id, track_id, liked, liked_at, updated_at) "
            "VALUES (?, 1, 1, ?, ?)",
            (store.user_id, explicit, utc_now()),
        )

    migrated = _reinit(db_path)

    with migrated.connect() as conn:
        liked_at = conn.execute(
            "SELECT liked_at FROM user_track_preferences WHERE track_id = 1"
        ).fetchone()["liked_at"]
    assert liked_at == explicit


def test_existing_entity_likes_survive_a_restart(tmp_path, monkeypatch):
    """Nothing clears likes at startup any more — only the starred sync does."""
    db_path = _configure_store(tmp_path, monkeypatch)
    store = Store(db_path)
    store.init()
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO user_artist_preferences (user_id, artist_id, liked, updated_at) "
            "VALUES (?, 100, 1, ?)",
            (store.user_id, utc_now()),
        )

    migrated = _reinit(db_path)

    with migrated.connect() as conn:
        liked = conn.execute(
            "SELECT liked FROM user_artist_preferences WHERE artist_id = 100"
        ).fetchone()["liked"]
    assert liked == 1
