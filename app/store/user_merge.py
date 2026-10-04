"""Merge case-variant duplicate users into one canonical account.

Part of the app/store package. Do not import this module directly; use
app.store instead.

Before usernames were matched case-insensitively, logging in as
"Infected2202" next to an existing "infected2202" created a second ``users``
row with its own history. Login, profiles and avatars already resolve to the
canonical row (lowest id, ``COLLATE NOCASE``); this one-time startup step folds
every duplicate into it and then adds a ``NOCASE`` unique index so a duplicate
can never be created again (see docs/social.md).

Per table, the duplicate's rows are:

* **moved** — history and owned things the canonical user never has a
  conflicting copy of (playback sessions/events, listens, auth sessions,
  playlists, shares). Generated mixes are moved too, but retired to ``stale``
  so they don't join the canonical user's live "Mixes For You".
* **canonical wins** — per-user state keyed by entity (preferences, settings,
  caches, flow profiles): rows the canonical user lacks are moved, rows it
  already has are dropped from the duplicate.

Any other table still referencing the duplicate aborts the whole merge (one
transaction, rolled back) rather than letting ``ON DELETE CASCADE`` drop data
nobody decided about. A ``VACUUM INTO`` backup is written before any change.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

USERNAME_NOCASE_INDEX = "idx_users_username_nocase"

# (table, user column): rows simply re-pointed at the canonical user.
_MOVE_TABLES: tuple[tuple[str, str], ...] = (
    ("playback_sessions", "user_id"),
    ("playback_events", "user_id"),
    ("listens", "user_id"),
    ("sessions", "user_id"),
    ("playlists", "user_id"),
    ("shares", "owner_user_id"),
    ("generated_mixes", "user_id"),
)

# Keyed per-user state: move what the canonical user lacks, drop the rest.
_CANONICAL_WINS_TABLES: tuple[str, ...] = (
    "user_track_preferences",
    "user_release_preferences",
    "user_artist_preferences",
    "user_label_preferences",
    "albums_for_you_cache",
    "user_settings",
    "flow_profiles",
)

_USER_COLUMNS = ("user_id", "owner_user_id")


def _nocase_key(username: str) -> str:
    """SQLite NOCASE folds ASCII letters only — group exactly like it does."""
    return username.translate(str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"))


def find_duplicate_users(conn: sqlite3.Connection) -> list[tuple[int, list[int]]]:
    """``[(canonical_id, [duplicate_ids…])]`` for usernames equal under NOCASE."""
    groups: dict[str, list[int]] = {}
    for user_id, username in conn.execute("SELECT id, navidrome_username FROM users ORDER BY id"):
        groups.setdefault(_nocase_key(str(username)), []).append(int(user_id))
    return [(ids[0], ids[1:]) for ids in groups.values() if len(ids) > 1]


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _merge_one(conn: sqlite3.Connection, canonical: int, duplicate: int) -> None:
    for table, column in _MOVE_TABLES:
        if column not in _table_columns(conn, table):
            continue
        if table == "generated_mixes":
            conn.execute(
                "UPDATE generated_mixes SET status = 'stale' WHERE user_id = ? AND status = 'active'",
                (duplicate,),
            )
        conn.execute(f"UPDATE {table} SET {column} = ? WHERE {column} = ?", (canonical, duplicate))
    for table in _CANONICAL_WINS_TABLES:
        if "user_id" not in _table_columns(conn, table):
            continue
        # A PRIMARY KEY/UNIQUE conflict with the canonical user's row skips the
        # update; whatever is left still belongs to the duplicate and is dropped.
        conn.execute(f"UPDATE OR IGNORE {table} SET user_id = ? WHERE user_id = ?", (canonical, duplicate))
        conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (duplicate,))

    leftovers: list[str] = []
    tables = [str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    for table in tables:
        if table == "users":
            continue
        for column in _USER_COLUMNS:
            if column in _table_columns(conn, table) and conn.execute(
                f"SELECT 1 FROM {table} WHERE {column} = ? LIMIT 1", (duplicate,)
            ).fetchone():
                leftovers.append(f"{table}.{column}")
    if leftovers:
        raise RuntimeError(
            f"Cannot merge duplicate user {duplicate} into {canonical}: no merge rule for "
            f"{', '.join(leftovers)} (add one in app/store/user_merge.py)"
        )
    conn.execute("DELETE FROM users WHERE id = ?", (duplicate,))


def _backup(db_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = db_path.with_name(f"{db_path.name}.pre-user-merge-{stamp}.bak")
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        conn.execute("VACUUM INTO ?", (str(target),))
    finally:
        conn.close()
    return target


def merge_case_variant_users(db_path: Path) -> list[tuple[int, int]]:
    """Fold duplicate users into their canonical row; ensure the NOCASE index.

    Returns the merged ``(canonical_id, duplicate_id)`` pairs. Cheap and
    idempotent when there is nothing to merge: one SELECT plus
    ``CREATE UNIQUE INDEX IF NOT EXISTS``.
    """
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        groups = find_duplicate_users(conn)
        merged: list[tuple[int, int]] = []
        if groups:
            backup = _backup(db_path)
            logger.warning("Merging case-variant duplicate users %s (backup: %s)", groups, backup)
            conn.execute("BEGIN IMMEDIATE")
            try:
                for canonical, duplicates in groups:
                    for duplicate in duplicates:
                        _merge_one(conn, canonical, duplicate)
                        merged.append((canonical, duplicate))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        conn.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {USERNAME_NOCASE_INDEX} "
            "ON users(navidrome_username COLLATE NOCASE)"
        )
        return merged
    finally:
        conn.close()
