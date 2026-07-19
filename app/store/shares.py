"""Store domain for revocable public capability links."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from uuid import uuid4

from app.models import SHARE_SOURCE_TYPES, Share, ShareItem, utc_now
from app.store._helpers import row_to_share, row_to_share_item


def generate_share_token() -> str:
    return secrets.token_urlsafe(32)


def share_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _active_at(share: Share, now: str) -> bool:
    if share.revoked_at is not None:
        return False
    if share.expires_at is None:
        return True
    try:
        return datetime.fromisoformat(share.expires_at) > datetime.fromisoformat(now)
    except ValueError:
        return False


class SharesStoreMixin:
    def create_share(
        self,
        *,
        source_type: str,
        source_id: int,
        expires_at: str | None,
        title: str | None = None,
    ) -> tuple[Share, str]:
        if source_type not in SHARE_SOURCE_TYPES:
            raise ValueError("Unsupported share source type")
        owner_user_id = self.require_user_id()
        now = utc_now()
        token = generate_share_token()
        token_hash = share_token_hash(token)
        share_id = str(uuid4())
        clean_title = title.strip() if title else None

        with self.connect() as conn:
            if source_type == "track":
                row = conn.execute(
                    "SELECT id FROM tracks WHERE id = ? AND missing_at IS NULL",
                    (int(source_id),),
                ).fetchone()
                track_ids = [int(row["id"])] if row is not None else []
            else:
                release = conn.execute(
                    "SELECT id FROM releases WHERE id = ?",
                    (int(source_id),),
                ).fetchone()
                if release is None:
                    track_ids = []
                else:
                    rows = conn.execute(
                        """
                        SELECT rt.track_id
                        FROM release_tracks rt
                        JOIN tracks t ON t.id = rt.track_id
                        WHERE rt.release_id = ? AND t.missing_at IS NULL
                        ORDER BY rt.position, rt.track_id
                        """,
                        (int(source_id),),
                    ).fetchall()
                    track_ids = [int(row["track_id"]) for row in rows]
            if not track_ids:
                raise ValueError("Share source has no available tracks")

            conn.execute(
                """
                INSERT INTO shares (
                    id, token_hash, token_prefix, owner_user_id, source_type,
                    source_id, title, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    share_id,
                    token_hash,
                    token[:6],
                    owner_user_id,
                    source_type,
                    int(source_id),
                    clean_title,
                    now,
                    expires_at,
                ),
            )
            conn.executemany(
                """
                INSERT INTO share_items (share_id, position, track_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (share_id, position, track_id, now)
                    for position, track_id in enumerate(dict.fromkeys(track_ids))
                ],
            )
            row = conn.execute("SELECT * FROM shares WHERE id = ?", (share_id,)).fetchone()
        return row_to_share(row), token

    def list_user_shares(self, *, include_revoked: bool = False) -> list[Share]:
        where = "owner_user_id = discocs_user_id()"
        if not include_revoked:
            where += " AND revoked_at IS NULL"
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM shares WHERE {where} ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return [row_to_share(row) for row in rows]

    def get_user_share(self, share_id: str) -> Share | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM shares WHERE id = ? AND owner_user_id = discocs_user_id()",
                (share_id,),
            ).fetchone()
        return row_to_share(row) if row is not None else None

    def update_user_share(
        self,
        share_id: str,
        *,
        title: str | None,
        expires_at: str | None,
    ) -> Share | None:
        clean_title = title.strip() if title else None
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE shares SET title = ?, expires_at = ?
                WHERE id = ? AND owner_user_id = discocs_user_id()
                  AND revoked_at IS NULL
                """,
                (clean_title, expires_at, share_id),
            )
            if cursor.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT * FROM shares WHERE id = ? AND owner_user_id = discocs_user_id()",
                (share_id,),
            ).fetchone()
        return row_to_share(row) if row is not None else None

    def revoke_user_share(self, share_id: str, *, revoked_at: str | None = None) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE shares SET revoked_at = COALESCE(revoked_at, ?)
                WHERE id = ? AND owner_user_id = discocs_user_id()
                """,
                (revoked_at or utc_now(), share_id),
            )
        return cursor.rowcount > 0

    def resolve_active_share(self, token_hash: str, *, now: str | None = None) -> Share | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM shares WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
        if row is None:
            return None
        share = row_to_share(row)
        return share if _active_at(share, now or utc_now()) else None

    def list_share_items(self, share_id: str) -> list[ShareItem]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM share_items WHERE share_id = ? ORDER BY position",
                (share_id,),
            ).fetchall()
        return [row_to_share_item(row) for row in rows]

    def get_active_share_item(
        self,
        token_hash: str,
        position: int,
        *,
        now: str | None = None,
    ) -> tuple[Share, ShareItem] | None:
        share = self.resolve_active_share(token_hash, now=now)
        if share is None:
            return None
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM share_items WHERE share_id = ? AND position = ?",
                (share.id, int(position)),
            ).fetchone()
        if row is None:
            return None
        return share, row_to_share_item(row)

    def share_item_count(self, share_id: str) -> int:
        with self.connect() as conn:
            return int(conn.execute(
                "SELECT COUNT(*) FROM share_items WHERE share_id = ?",
                (share_id,),
            ).fetchone()[0])

    def touch_share_access(self, share_id: str, *, now: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE shares
                SET last_accessed_at = ?, access_count = access_count + 1
                WHERE id = ?
                """,
                (now or utc_now(), share_id),
            )
