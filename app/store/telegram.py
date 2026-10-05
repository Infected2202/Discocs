"""Store domain: Telegram bot account links (docs/telegram.md).

Part of the app/store package. Do not import this module directly; use
app.store instead.

A user starts linking from the web UI: the backend mints a one-shot token that
travels to the bot inside a ``t.me/<bot>?start=link_<token>`` deep link, and
the bot redeems it together with the Telegram user id it saw. The web session
already proved who the user is, so no password ever passes through Telegram.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta

from app.models import utc_now


def telegram_link_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TelegramStoreMixin:
    def create_telegram_link_token(self, *, ttl_minutes: int) -> tuple[str, str]:
        """Mint a link token for the bound user; return ``(token, expires_at)``.

        Earlier unredeemed tokens of the same user are dropped, so only the
        most recently shown deep link works.
        """
        user_id = self.require_user_id()
        now = datetime.now(UTC)
        expires_at = (now + timedelta(minutes=ttl_minutes)).isoformat()
        # 24 bytes → 32 URL-safe chars: fits the 64-char /start payload with
        # the "link_" prefix and stays within its [A-Za-z0-9_-] alphabet.
        token = secrets.token_urlsafe(24)
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM telegram_link_tokens WHERE user_id = ? OR expires_at <= ?",
                (user_id, now.isoformat()),
            )
            conn.execute(
                """
                INSERT INTO telegram_link_tokens (token_hash, user_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (telegram_link_token_hash(token), user_id, now.isoformat(), expires_at),
            )
        return token, expires_at

    def redeem_telegram_link_token(
        self,
        token: str,
        *,
        telegram_user_id: int,
        telegram_username: str | None,
    ) -> str | None:
        """Bind the token's user to ``telegram_user_id``; return the username.

        The token is consumed whatever the outcome. ``None`` means unknown or
        expired. A Telegram account linked elsewhere moves to the new user, and
        the user's previous Telegram account (if any) is replaced.
        """
        token_hash = telegram_link_token_hash(token)
        now = utc_now()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT user_id, expires_at FROM telegram_link_tokens WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            conn.execute("DELETE FROM telegram_link_tokens WHERE token_hash = ?", (token_hash,))
            if row is None or not _still_valid(str(row["expires_at"]), now):
                return None
            user_id = int(row["user_id"])
            conn.execute(
                "DELETE FROM telegram_links WHERE telegram_user_id = ? OR user_id = ?",
                (int(telegram_user_id), user_id),
            )
            conn.execute(
                """
                INSERT INTO telegram_links (user_id, telegram_user_id, telegram_username, linked_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, int(telegram_user_id), telegram_username or None, now),
            )
            user = conn.execute(
                "SELECT navidrome_username FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return str(user["navidrome_username"]) if user is not None else None

    def get_own_telegram_link(self) -> sqlite3.Row | None:
        user_id = self.require_user_id()
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM telegram_links WHERE user_id = ?", (user_id,)
            ).fetchone()

    def delete_own_telegram_link(self) -> bool:
        user_id = self.require_user_id()
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM telegram_links WHERE user_id = ?", (user_id,))
        return cursor.rowcount > 0


def _still_valid(expires_at: str, now: str) -> bool:
    try:
        return datetime.fromisoformat(expires_at) > datetime.fromisoformat(now)
    except ValueError:
        return False
