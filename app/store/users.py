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
            conn.execute(
                """
                INSERT INTO users (navidrome_username, created_at, last_login_at)
                VALUES (?, ?, ?)
                ON CONFLICT(navidrome_username)
                DO UPDATE SET last_login_at = excluded.last_login_at
                """,
                (navidrome_username, now, now),
            )
            row = conn.execute(
                "SELECT id FROM users WHERE navidrome_username = ?",
                (navidrome_username,),
            ).fetchone()
            return int(row["id"])

    def get_user_by_username(self, navidrome_username: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM users WHERE navidrome_username = ?",
                (navidrome_username,),
            ).fetchone()

    def get_user_by_id(self, user_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
