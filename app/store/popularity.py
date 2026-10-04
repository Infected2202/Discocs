"""Store Popularity domain: Deezer rank/fans for tracks and releases.

Part of the app/store package.
Do not import this module directly; use app.store instead.

Связь трека с Deezer приходит снаружи (``recs deezer-import``: ID трека и альбома
Deezer записаны в тегах файлов, а Navidrome их не отдаёт), снимки rank/fans
освежает ``app.popularity.refresh_deezer_popularity``.
"""
from __future__ import annotations

from app.models import utc_now

NAVIDROME_PROVIDER = "navidrome"


class PopularityStoreMixin:
    def import_deezer_links(
        self,
        tracks: list[dict[str, object]],
        albums: list[dict[str, object]],
        *,
        now: str | None = None,
    ) -> dict[str, int]:
        """Связи «трек Navidrome → трек/альбом Deezer» и снимки fans альбомов.

        ``tracks``: ``navidrome_id``, ``deezer_album_id``, ``deezer_track_id`` и
        ``rank`` (оба необязательны). ``albums``: ``deezer_album_id``, ``fans``,
        ``fetched_at`` — без ``fetched_at`` альбом только заводится, и фоновое
        обновление возьмёт его первым. Снимок не затирается более старым.
        """
        now = now or utc_now()
        stats = {"tracks": 0, "unknown_tracks": 0, "albums": 0}
        with self.connect() as conn:
            for item in tracks:
                row = conn.execute(
                    """
                    SELECT entity_id FROM external_ids
                    WHERE provider = ? AND entity_type = 'track' AND external_id = ?
                    """,
                    (NAVIDROME_PROVIDER, str(item["navidrome_id"])),
                ).fetchone()
                if row is None:
                    stats["unknown_tracks"] += 1
                    continue
                conn.execute(
                    """
                    INSERT INTO track_deezer (track_id, deezer_track_id, deezer_album_id, rank, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(track_id) DO UPDATE SET
                        deezer_track_id = COALESCE(excluded.deezer_track_id, track_deezer.deezer_track_id),
                        deezer_album_id = excluded.deezer_album_id,
                        rank = COALESCE(excluded.rank, track_deezer.rank),
                        updated_at = excluded.updated_at
                    """,
                    (
                        int(row["entity_id"]),
                        _optional_int(item.get("deezer_track_id")),
                        int(item["deezer_album_id"]),
                        _optional_int(item.get("rank")),
                        now,
                    ),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO deezer_albums (deezer_album_id) VALUES (?)",
                    (int(item["deezer_album_id"]),),
                )
                stats["tracks"] += 1
            for item in albums:
                fetched_at = item.get("fetched_at")
                conn.execute(
                    "INSERT OR IGNORE INTO deezer_albums (deezer_album_id) VALUES (?)",
                    (int(item["deezer_album_id"]),),
                )
                if fetched_at:
                    conn.execute(
                        """
                        UPDATE deezer_albums SET fans = ?, fetched_at = ?, error = NULL
                        WHERE deezer_album_id = ? AND (fetched_at IS NULL OR fetched_at < ?)
                        """,
                        (_optional_int(item.get("fans")), str(fetched_at), int(item["deezer_album_id"]), str(fetched_at)),
                    )
                stats["albums"] += 1
        return stats

    def stale_deezer_albums(self, older_than: str, limit: int) -> list[int]:
        """Альбомы Deezer, связанные с треками, чей снимок старше ``older_than`` (или его нет)."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT da.deezer_album_id
                FROM deezer_albums da
                WHERE (da.fetched_at IS NULL OR da.fetched_at < ?)
                  AND EXISTS (SELECT 1 FROM track_deezer td WHERE td.deezer_album_id = da.deezer_album_id)
                ORDER BY da.fetched_at IS NOT NULL, da.fetched_at, da.deezer_album_id
                LIMIT ?
                """,
                (older_than, limit),
            ).fetchall()
        return [int(row["deezer_album_id"]) for row in rows]

    def save_deezer_album_snapshot(
        self,
        deezer_album_id: int,
        *,
        fans: int | None,
        track_ranks: dict[int, int],
        error: str | None = None,
        now: str | None = None,
    ) -> None:
        """Свежий снимок альбома; ``error`` — Deezer альбом не отдал (снят и т.п.), прежние числа остаются."""
        now = now or utc_now()
        with self.connect() as conn:
            if error is not None:
                conn.execute(
                    "UPDATE deezer_albums SET fetched_at = ?, error = ? WHERE deezer_album_id = ?",
                    (now, error, deezer_album_id),
                )
                return
            conn.execute(
                "UPDATE deezer_albums SET fans = ?, fetched_at = ?, error = NULL WHERE deezer_album_id = ?",
                (fans, now, deezer_album_id),
            )
            conn.executemany(
                """
                UPDATE track_deezer SET rank = ?, updated_at = ?
                WHERE deezer_album_id = ? AND deezer_track_id = ?
                """,
                [(rank, now, deezer_album_id, track_id) for track_id, rank in track_ranks.items()],
            )

    def release_popularity(self, release_ids: list[int]) -> dict[int, int]:
        """fans альбома Deezer по релизам (наибольший, если треки релиза из разных альбомов Deezer)."""
        if not release_ids:
            return {}
        placeholders = ",".join("?" for _ in release_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT rt.release_id, MAX(da.fans) AS fans
                FROM release_tracks rt
                JOIN track_deezer td ON td.track_id = rt.track_id
                JOIN deezer_albums da ON da.deezer_album_id = td.deezer_album_id
                WHERE rt.release_id IN ({placeholders}) AND da.fans IS NOT NULL
                GROUP BY rt.release_id
                """,
                list(release_ids),
            ).fetchall()
        return {int(row["release_id"]): int(row["fans"]) for row in rows}

    def deezer_popularity_stats(self) -> dict[str, int]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM track_deezer) AS tracks,
                    (SELECT COUNT(*) FROM track_deezer WHERE rank IS NOT NULL) AS ranked_tracks,
                    (SELECT COUNT(*) FROM deezer_albums) AS albums,
                    (SELECT COUNT(*) FROM deezer_albums WHERE fetched_at IS NOT NULL) AS fetched_albums,
                    (SELECT COUNT(*) FROM deezer_albums WHERE error IS NOT NULL) AS failed_albums
                """
            ).fetchone()
        return {key: int(row[key]) for key in row.keys()}


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)  # type: ignore[arg-type]
