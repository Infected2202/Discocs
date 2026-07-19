"""Phase 1 of the likes unification: liked_at column and the one-shot cleanup.

See plans/likes-unification-plan.md. The cleanup drops release/artist likes that
were inherited from track stars; Navidrome restores the real ones on the next
starred/ids call.
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


def _seed_likes(store: Store, *, liked_at: str | None = None) -> None:
    now = utc_now()
    user_id = store.user_id
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO user_track_preferences (user_id, track_id, liked, liked_at, updated_at) "
            "VALUES (?, 1, 1, ?, ?)",
            (user_id, liked_at, now),
        )
        conn.execute(
            "INSERT INTO user_release_preferences (user_id, release_id, liked, liked_at, updated_at) "
            "VALUES (?, 10, 1, ?, ?)",
            (user_id, liked_at, now),
        )
        conn.execute(
            "INSERT INTO user_artist_preferences (user_id, artist_id, liked, liked_at, updated_at) "
            "VALUES (?, 100, 1, ?, ?)",
            (user_id, liked_at, now),
        )


def _liked_flags(store: Store) -> dict[str, int]:
    with store.connect() as conn:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table} WHERE liked = 1").fetchone()[0]
            for table in (
                "user_track_preferences",
                "user_release_preferences",
                "user_artist_preferences",
            )
        }


def test_liked_at_column_exists_on_all_preference_tables(tmp_path, monkeypatch):
    db_path = _configure_store(tmp_path, monkeypatch)
    store = Store(db_path)
    store.init()

    with store.connect() as conn:
        for table in (
            "user_track_preferences",
            "user_release_preferences",
            "user_artist_preferences",
        ):
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            assert "liked_at" in columns, table


def test_cleanup_clears_inherited_entity_likes_but_keeps_track_likes(tmp_path, monkeypatch):
    db_path = _configure_store(tmp_path, monkeypatch)
    store = Store(db_path)
    store.init()
    _seed_likes(store)
    # Rewind the gate so the seeded rows look like a pre-migration database.
    with store.connect() as conn:
        conn.execute("PRAGMA user_version = 0")

    migrated = _reinit(db_path)

    assert _liked_flags(migrated) == {
        "user_track_preferences": 1,
        "user_release_preferences": 0,
        "user_artist_preferences": 0,
    }


def test_cleanup_also_clears_liked_at_of_dropped_likes(tmp_path, monkeypatch):
    db_path = _configure_store(tmp_path, monkeypatch)
    store = Store(db_path)
    store.init()
    _seed_likes(store, liked_at=utc_now())
    with store.connect() as conn:
        conn.execute("PRAGMA user_version = 0")

    migrated = _reinit(db_path)

    with migrated.connect() as conn:
        for table in ("user_release_preferences", "user_artist_preferences"):
            stale = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE liked_at IS NOT NULL"
            ).fetchone()[0]
            assert stale == 0, table


def test_cleanup_does_not_run_twice_and_spares_later_likes(tmp_path, monkeypatch):
    """The gate is the whole point: a second start must not wipe real likes."""
    db_path = _configure_store(tmp_path, monkeypatch)
    store = Store(db_path)
    store.init()
    # First init already advanced user_version; these are post-migration likes.
    _seed_likes(store)

    migrated = _reinit(db_path)

    assert _liked_flags(migrated) == {
        "user_track_preferences": 1,
        "user_release_preferences": 1,
        "user_artist_preferences": 1,
    }


def test_liked_at_backfilled_from_updated_at_for_existing_likes(tmp_path, monkeypatch):
    db_path = _configure_store(tmp_path, monkeypatch)
    store = Store(db_path)
    store.init()
    _seed_likes(store)

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
    _seed_likes(store, liked_at=explicit)

    migrated = _reinit(db_path)

    with migrated.connect() as conn:
        liked_at = conn.execute(
            "SELECT liked_at FROM user_track_preferences WHERE track_id = 1"
        ).fetchone()["liked_at"]
    assert liked_at == explicit
