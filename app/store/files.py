"""Store Files domain: missing-file tracking, track listing, search, browse.

Part of the app/store package — Stage 7 refactoring.
Do not import this module directly; use app.store instead.
"""
from __future__ import annotations

from __future__ import annotations

import logging
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta
try:
    from datetime import UTC
except ImportError:
    from datetime import timezone as _tz; UTC = _tz.utc
from pathlib import Path
from threading import Lock
from uuid import uuid4

import numpy as np

from app.library import (
    ArtistCredit,
    TrackMetadataEnvelope,
    clean_display_text,
    envelope_from_scanned_track,
    envelope_from_track_row,
    normalize_text,
    parse_artist_credit,
    release_identity_key,
    release_title_for_envelope,
)
from app.models import (
    AnalysisJob,
    AnalysisTask,
    AnalysisWorker,
    Artist,
    ArtistSummaryRow,
    COMPLETION_FRACTION,
    EARLY_SKIP_FRACTION,
    EARLY_SKIP_SECONDS,
    ExternalTrack,
    FeatureFilter,
    FeatureSummary,
    FeatureTrack,
    GeneratedMix,
    GeneratedMixItem,
    GENERATED_MIX_STATUSES,
    GENERATED_MIX_TYPES,
    HeadSummary,
    InstantMixRequest,
    LATE_SKIP_FRACTION,
    MEANINGFUL_LISTEN_FRACTION,
    MEANINGFUL_LISTEN_SECONDS,
    NormalizationStatus,
    Playlist,
    PlaylistItem,
    PlaybackEvent,
    PLAYBACK_EVENT_TYPES,
    PlaybackEventResult,
    PLAYBACK_MODES,
    PLAYBACK_REPEAT_MODES,
    PlaybackSession,
    PLAYBACK_SESSION_STATUSES,
    PLAYBACK_SOURCE_TYPES,
    QueueItem,
    QUEUE_ORIGINS,
    QUEUE_STATUSES,
    Release,
    ReleaseSummaryRow,
    ReleaseTrackRow,
    SimilarTrack,
    Track,
    TrackFeature,
    TrackListing,
    TrackModelOutput,
    TrackPrediction,
    UserArtistPreference,
    UserReleasePreference,
    UserTrackPreference,
    utc_now,
)
from app.scanner import ScannedTrack
from app.store._helpers import row_to_track


logger = logging.getLogger(__name__)
INIT_LOCK = Lock()
INITIALIZED_DB_PATHS: set[Path] = set()
_ORDER_COUNT_DESC_VALUE = "count DESC, value"

class FilesStoreMixin:
    def mark_track_missing(self, track_id: int, missing_at: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE tracks SET missing_at = ?, updated_at = ? WHERE id = ?",
                (missing_at or utc_now(), utc_now(), track_id),
            )

    def mark_track_available(self, track_id: int) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "UPDATE tracks SET missing_at = NULL, last_seen_at = ?, updated_at = ? WHERE id = ?",
                (now, now, track_id),
            )

    def check_file_availability(self) -> tuple[int, int]:
        checked = 0
        missing = 0
        now = utc_now()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    t.id,
                    t.path,
                    EXISTS (
                        SELECT 1
                        FROM external_tracks e
                        WHERE e.track_id = t.id
                          AND e.provider = 'navidrome'
                    ) AS is_navidrome
                FROM tracks t
                ORDER BY t.id
                """
            ).fetchall()
            for row in rows:
                checked += 1
                track_id = int(row["id"])
                track_path = str(row["path"])
                if row["is_navidrome"] or track_path.startswith("navidrome://"):
                    conn.execute(
                        """
                        UPDATE tracks
                        SET missing_at = NULL, last_seen_at = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (now, now, track_id),
                    )
                    continue
                path = Path(track_path)
                if path.exists() and path.is_file():
                    conn.execute(
                        """
                        UPDATE tracks
                        SET missing_at = NULL, last_seen_at = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (now, now, track_id),
                    )
                else:
                    missing += 1
                    conn.execute(
                        """
                        UPDATE tracks
                        SET missing_at = COALESCE(missing_at, ?), updated_at = ?
                        WHERE id = ?
                        """,
                        (now, now, track_id),
                    )
        return checked, missing

    def list_missing_tracks(self, limit: int = 500, offset: int = 0) -> list[Track]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM tracks
                WHERE missing_at IS NOT NULL
                ORDER BY missing_at DESC, id DESC
                LIMIT ?
                OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return [row_to_track(row) for row in rows]

    def count_missing_files(self) -> int:
        with self.connect() as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM tracks WHERE missing_at IS NOT NULL"
                ).fetchone()[0]
            )

    def delete_tracks(self, track_ids: list[int]) -> int:
        if not track_ids:
            return 0
        placeholders = ",".join("?" for _track_id in track_ids)
        with self.connect() as conn:
            cursor = conn.execute(
                f"DELETE FROM tracks WHERE id IN ({placeholders})",
                [int(track_id) for track_id in track_ids],
            )
            return int(cursor.rowcount)

    def delete_missing_tracks(self) -> int:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM tracks WHERE missing_at IS NOT NULL")
            return int(cursor.rowcount)

    def find_track_by_path(self, path: Path) -> Track | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM tracks WHERE path = ?",
                (str(path.resolve()),),
            ).fetchone()
        return row_to_track(row) if row else None

    def list_tracks(
        self,
        query: str = "",
        limit: int = 50,
        model_name: str = "discogs_multi",
        embedding_status: str = "all",
        folder: str | None = None,
        genre: str | None = None,
        year: int | None = None,
        artist: str | None = None,
        album: str | None = None,
    ) -> list[TrackListing]:
        if embedding_status not in {"all", "ready", "missing"}:
            raise ValueError("embedding_status must be all, ready, or missing")
        like = f"%{query}%"
        filters: list[str] = []
        params: list[object] = [model_name]
        if query:
            filters.append(
                "(t.artist LIKE ? OR t.title LIKE ? OR t.album LIKE ? OR "
                "t.genre LIKE ? OR t.path LIKE ?)"
            )
            params.extend([like, like, like, like, like])
        if folder:
            clean_folder = folder.rstrip("\\/")
            filters.append("(t.path LIKE ? OR t.path LIKE ?)")
            params.extend([f"{clean_folder}\\%", f"{clean_folder}/%"])
        if genre:
            filters.append("t.genre = ?")
            params.append(genre)
        if year is not None:
            filters.append("t.year = ?")
            params.append(year)
        if artist:
            filters.append("t.artist = ?")
            params.append(artist)
        if album:
            filters.append("t.album = ?")
            params.append(album)
        if embedding_status == "ready":
            filters.append("e.track_id IS NOT NULL")
        elif embedding_status == "missing":
            filters.append("e.track_id IS NULL")
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT t.*, e.track_id AS embedding_track_id
                FROM tracks t
                LEFT JOIN embeddings e
                  ON e.track_id = t.id AND e.model_name = ?
                {where}
                ORDER BY CASE WHEN ? = '' THEN t.id END DESC, t.artist, t.title
                LIMIT ?
                """,
                [*params[:-1], query, params[-1]],
            ).fetchall()
        return [
            TrackListing(
                track=row_to_track(row),
                has_embedding=row["embedding_track_id"] is not None,
            )
            for row in rows
        ]

    def search_tracks(self, query: str, limit: int = 50) -> list[Track]:
        return [listing.track for listing in self.list_tracks(query=query, limit=limit)]

    def browser_facets(
        self,
        model_name: str = "discogs_multi",
        embedding_status: str = "all",
        limit: int = 80,
    ) -> dict[str, list[dict[str, object]]]:
        if embedding_status not in {"all", "ready", "missing"}:
            raise ValueError("embedding_status must be all, ready, or missing")
        filters: list[str] = []
        if embedding_status == "ready":
            filters.append("e.track_id IS NOT NULL")
        elif embedding_status == "missing":
            filters.append("e.track_id IS NULL")
        where = f"WHERE {' AND '.join(filters)}" if filters else ""

        def facet_rows(column: str, extra_where: str, order_by: str) -> list[dict[str, object]]:
            with self.connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT t.{column} AS value, COUNT(*) AS count
                    FROM tracks t
                    LEFT JOIN embeddings e
                      ON e.track_id = t.id AND e.model_name = ?
                    {where}
                    {"AND" if where else "WHERE"} {extra_where}
                    GROUP BY t.{column}
                    ORDER BY {order_by}
                    LIMIT ?
                    """,
                    (model_name, limit),
                ).fetchall()
            return [{"value": row["value"], "count": int(row["count"])} for row in rows]

        with self.connect() as conn:
            path_rows = conn.execute(
                f"""
                SELECT t.path
                FROM tracks t
                LEFT JOIN embeddings e
                  ON e.track_id = t.id AND e.model_name = ?
                {where}
                """,
                (model_name,),
            ).fetchall()

        folder_counts: dict[str, int] = {}
        for row in path_rows:
            folder = str(Path(str(row["path"])).parent)
            folder_counts[folder] = folder_counts.get(folder, 0) + 1

        return {
            "folders": [
                {"value": value, "count": count}
                for value, count in sorted(
                    folder_counts.items(),
                    key=lambda item: (-item[1], item[0].lower()),
                )[:limit]
            ],
            "genres": facet_rows(
                "genre",
                "t.genre IS NOT NULL AND TRIM(t.genre) != ''",
                _ORDER_COUNT_DESC_VALUE,
            ),
            "years": facet_rows("year", "t.year IS NOT NULL", "value DESC"),
            "artists": facet_rows(
                "artist",
                "t.artist IS NOT NULL AND TRIM(t.artist) != ''",
                _ORDER_COUNT_DESC_VALUE,
            ),
            "albums": facet_rows(
                "album",
                "t.album IS NOT NULL AND TRIM(t.album) != ''",
                _ORDER_COUNT_DESC_VALUE,
            ),
        }

    def list_tracks_missing_embedding(self, model_name: str, limit: int | None = None) -> list[Track]:
        sql = """
            SELECT t.* FROM tracks t
            LEFT JOIN embeddings e
              ON e.track_id = t.id AND e.model_name = ?
            WHERE e.track_id IS NULL
              AND t.missing_at IS NULL
            ORDER BY t.id
        """
        params: list[object] = [model_name]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [row_to_track(row) for row in rows]

    def list_active_tracks(self, limit: int | None = None) -> list[Track]:
        sql = """
            SELECT t.* FROM tracks t
            WHERE t.missing_at IS NULL
            ORDER BY t.id
        """
        params: list[object] = []
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [row_to_track(row) for row in rows]

