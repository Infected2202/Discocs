"""Store Users domain: application user identity (Phase 2 multiuser).

Part of the app/store package. Do not import this module directly; use
app.store instead.

Identity is the Navidrome login. A ``users`` row is created automatically on
the first successful login (upsert by ``navidrome_username``) — there is no
registration form and no in-app allowlist; access is controlled by who has
Navidrome credentials. The internal integer ``id`` is the FK used to scope
personal tables. See docs/auth.md and plans/multiuser-spec.md §1/§9.
"""
from __future__ import annotations

import sqlite3

from app.avatars import AVATAR_SETTING_KEY
from app.models import utc_now


class UsersStoreMixin:
    def list_user_ids(self) -> list[int]:
        with self.connect() as conn:
            rows = conn.execute("SELECT id FROM users ORDER BY id").fetchall()
        return [int(row["id"]) for row in rows]

    def upsert_user(self, navidrome_username: str, *, now: str) -> int:
        """Insert-or-touch a user by Navidrome username; return internal id.

        First call for a username creates the row (``created_at`` and
        ``last_login_at`` = ``now``); subsequent calls only bump
        ``last_login_at``. Idempotent, safe to call on every login.
        """
        if not navidrome_username:
            raise ValueError("navidrome_username must be non-empty")
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM users "
                "WHERE navidrome_username = ? COLLATE NOCASE ORDER BY id LIMIT 1",
                (navidrome_username,),
            ).fetchone()
            if row is not None:
                user_id = int(row["id"])
                conn.execute(
                    "UPDATE users SET last_login_at = ? WHERE id = ?",
                    (now, user_id),
                )
                return user_id
            cursor = conn.execute(
                """
                INSERT INTO users (navidrome_username, created_at, last_login_at)
                VALUES (?, ?, ?)
                """,
                (navidrome_username, now, now),
            )
            return int(cursor.lastrowid)

    def get_user_by_username(self, navidrome_username: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM users "
                "WHERE navidrome_username = ? COLLATE NOCASE ORDER BY id LIMIT 1",
                (navidrome_username,),
            ).fetchone()

    def get_user_by_id(self, user_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()

    def list_users(self) -> list[sqlite3.Row]:
        """Every discocs user (identity columns only), by username."""
        with self.connect() as conn:
            return conn.execute(
                "SELECT id, navidrome_username, created_at, last_login_at FROM users "
                "ORDER BY navidrome_username COLLATE NOCASE, id"
            ).fetchall()

    # Avatars are the one public entry of user_settings (app/avatars.py): they
    # are read for any user by id, unlike the default-deny personal settings.

    def get_user_avatar(self, user_id: int) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM user_settings WHERE user_id = ? AND key = ?",
                (int(user_id), AVATAR_SETTING_KEY),
            ).fetchone()
        return str(row["value"]) if row is not None else None

    def assign_default_avatar(
        self,
        user_id: int,
        key: str,
        *,
        replace_value: str | None = None,
    ) -> str:
        """Store ``key`` unless the user already has an avatar; return the stored one.

        An existing value is overwritten only while it still equals
        ``replace_value`` (a key that left the whitelist), so a concurrent
        first assignment or the user's own choice always wins.
        """
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO user_settings (user_id, key, value, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, key) DO UPDATE
                SET value = excluded.value, updated_at = excluded.updated_at
                WHERE user_settings.value IS ?
                """,
                (int(user_id), AVATAR_SETTING_KEY, key, utc_now(), replace_value),
            )
            row = conn.execute(
                "SELECT value FROM user_settings WHERE user_id = ? AND key = ?",
                (int(user_id), AVATAR_SETTING_KEY),
            ).fetchone()
        return str(row["value"])

    def set_own_avatar(self, key: str) -> str:
        """Set the current user's avatar; only ever writes discocs_user_id()'s row."""
        self.require_user_id()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO user_settings (user_id, key, value, updated_at)
                VALUES (discocs_user_id(), ?, ?, ?)
                ON CONFLICT(user_id, key)
                DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (AVATAR_SETTING_KEY, key, utc_now()),
            )
        return key
