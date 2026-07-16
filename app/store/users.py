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
