"""Store Sessions domain: auth session persistence.

Part of the app/store package — Phase 1 auth gate.
Do not import this module directly; use app.store instead.

Only the SHA-256 hash of a session token is stored. The optional Navidrome
password ciphertext cannot be decrypted without the raw token held by client.
"""
from __future__ import annotations

import sqlite3


class SessionsStoreMixin:
    def create_session(
        self,
        *,
        token_hash: str,
        username: str,
        created_at: str,
        expires_at: str,
        ip: str | None,
        user_agent: str | None,
        user_id: int | None = None,
        nav_secret: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    token_hash, username, user_id, created_at, expires_at,
                    last_seen_at, ip, user_agent, nav_secret
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token_hash, username, user_id, created_at, expires_at,
                    created_at, ip, user_agent, nav_secret,
                ),
            )

    def get_session(self, token_hash: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM sessions WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()

    def touch_session(self, token_hash: str, last_seen_at: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?",
                (last_seen_at, token_hash),
            )

    def delete_session(self, token_hash: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def purge_expired_sessions(self, now: str) -> int:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            return int(cursor.rowcount)
