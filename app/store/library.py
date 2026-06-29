"""Store Library domain: tracks, artists, releases, external IDs, normalization.

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

from app.store._helpers import (
    _discography_group_key,
    _envelope_from_track_with_external,
    _require_external_value,
    row_to_artist,
    row_to_external_track,
    row_to_release,
    row_to_track,
)
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


logger = logging.getLogger(__name__)
INIT_LOCK = Lock()
INITIALIZED_DB_PATHS: set[Path] = set()

class LibraryStoreMixin:
    def upsert_track(self, scanned: ScannedTrack) -> tuple[int, bool]:
        now = utc_now()
        path = str(scanned.path)
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT id, file_size, mtime FROM tracks WHERE path = ?",
                (path,),
            ).fetchone()
            if existing:
                changed = (
                    int(existing["file_size"]) != scanned.file_size
                    or int(existing["mtime"]) != scanned.mtime
                )
                conn.execute(
                    """
                    UPDATE tracks
                    SET artist = ?, title = ?, album = ?, genre = ?, year = ?, duration = ?,
                        file_size = ?, mtime = ?, missing_at = NULL, last_seen_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        scanned.artist,
                        scanned.title,
                        scanned.album,
                        scanned.genre,
                        scanned.year,
                        scanned.duration,
                        scanned.file_size,
                        scanned.mtime,
                        now,
                        now,
                        existing["id"],
                    ),
                )
                if changed:
                    logger.info(
                        "Track changed, invalidating derived data track_id=%s path=%s",
                        existing["id"],
                        path,
                    )
                    conn.execute("DELETE FROM embeddings WHERE track_id = ?", (existing["id"],))
                    conn.execute(
                        "DELETE FROM track_model_outputs WHERE track_id = ?",
                        (existing["id"],),
                    )
                    conn.execute(
                        "DELETE FROM track_predictions WHERE track_id = ?",
                        (existing["id"],),
                    )
                    conn.execute(
                        "DELETE FROM track_features WHERE track_id = ?",
                        (existing["id"],),
                    )
                self._upsert_normalized_track_sidecars(
                    conn,
                    int(existing["id"]),
                    envelope_from_scanned_track(scanned),
                )
                return int(existing["id"]), changed

            cursor = conn.execute(
                """
                INSERT INTO tracks (
                    path, artist, title, album, genre, year, duration, file_size, mtime,
                    missing_at, last_seen_at, added_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    path,
                    scanned.artist,
                    scanned.title,
                    scanned.album,
                    scanned.genre,
                    scanned.year,
                    scanned.duration,
                    scanned.file_size,
                    scanned.mtime,
                    now,
                    now,
                    now,
                    now,
                ),
            )
            track_id = int(cursor.lastrowid)
            self._upsert_normalized_track_sidecars(
                conn,
                track_id,
                envelope_from_scanned_track(scanned),
            )
            return track_id, True

    def upsert_normalized_track_sidecars(
        self,
        track_id: int,
        envelope: TrackMetadataEnvelope,
    ) -> int:
        with self.connect() as conn:
            return self._upsert_normalized_track_sidecars(conn, track_id, envelope)

    def _upsert_normalized_track_sidecars(
        self,
        conn: sqlite3.Connection,
        track_id: int,
        envelope: TrackMetadataEnvelope,
    ) -> int:
        if conn.execute("SELECT 1 FROM tracks WHERE id = ?", (track_id,)).fetchone() is None:
            raise ValueError(f"Track not found: {track_id}")
        now = utc_now()
        track_artists = parse_artist_credit(envelope.artist)
        identity_key, identity_confidence = release_identity_key(envelope)
        release_id = self._upsert_release(
            conn,
            envelope=envelope,
            identity_key=identity_key,
            identity_confidence=identity_confidence,
            now=now,
        )

        position = envelope.track_number or track_id
        previous_release_ids = [
            int(row["release_id"])
            for row in conn.execute(
                "SELECT release_id FROM release_tracks WHERE track_id = ?",
                (track_id,),
            ).fetchall()
        ]
        conn.execute("DELETE FROM release_tracks WHERE track_id = ?", (track_id,))
        while conn.execute(
            "SELECT 1 FROM release_tracks WHERE release_id = ? AND position = ?",
            (release_id, position),
        ).fetchone():
            position += 1
        conn.execute(
            """
            INSERT INTO release_tracks (
                release_id, track_id, disc_number, track_number, position,
                title_override, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
            ON CONFLICT(release_id, track_id) DO UPDATE SET
                disc_number = excluded.disc_number,
                track_number = excluded.track_number,
                position = excluded.position,
                updated_at = excluded.updated_at
            """,
            (
                release_id,
                track_id,
                envelope.disc_number,
                envelope.track_number,
                position,
                now,
                now,
            ),
        )

        track_artist_ids: list[int] = []
        conn.execute("DELETE FROM track_artists WHERE track_id = ?", (track_id,))
        for credit in track_artists:
            artist_id = self._upsert_artist(conn, credit.name, now)
            track_artist_ids.append(artist_id)
            self._insert_track_artist(conn, track_id, artist_id, credit, now)

        self._refresh_release_artists(
            conn,
            release_id,
            explicit_credit=envelope.album_artist,
            now=now,
        )

        if envelope.provider and envelope.provider_track_id:
            self._upsert_external_id(
                conn,
                provider=envelope.provider,
                entity_type="track",
                entity_id=track_id,
                external_id=envelope.provider_track_id,
                raw_json=envelope.raw_json,
                synced_at=now,
            )
        if envelope.provider and envelope.provider_release_id:
            self._upsert_external_id(
                conn,
                provider=envelope.provider,
                entity_type="release",
                entity_id=release_id,
                external_id=envelope.provider_release_id,
                raw_json=envelope.raw_json,
                synced_at=now,
            )
        if envelope.provider and envelope.provider_artist_id and track_artist_ids:
            self._upsert_external_id(
                conn,
                provider=envelope.provider,
                entity_type="artist",
                entity_id=track_artist_ids[0],
                external_id=envelope.provider_artist_id,
                raw_json=envelope.raw_json,
                synced_at=now,
            )

        self._refresh_release_basics(conn, release_id, now)
        for previous_release_id in previous_release_ids:
            if previous_release_id != release_id:
                self._refresh_release_after_track_removal(conn, previous_release_id, now)
        return release_id

    def _upsert_artist(self, conn: sqlite3.Connection, name: str, now: str) -> int:
        display_name = clean_display_text(name) or "Unknown Artist"
        normalized_name = normalize_text(display_name)
        row = conn.execute(
            "SELECT id FROM artists WHERE normalized_name = ?",
            (normalized_name,),
        ).fetchone()
        if row is not None:
            conn.execute(
                "UPDATE artists SET name = ?, sort_name = ?, updated_at = ? WHERE id = ?",
                (display_name, display_name, now, row["id"]),
            )
            return int(row["id"])
        cursor = conn.execute(
            """
            INSERT INTO artists (name, sort_name, normalized_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (display_name, display_name, normalized_name, now, now),
        )
        return int(cursor.lastrowid)

    def _upsert_release(
        self,
        conn: sqlite3.Connection,
        *,
        envelope: TrackMetadataEnvelope,
        identity_key: str,
        identity_confidence: str,
        now: str,
    ) -> int:
        title = release_title_for_envelope(envelope)
        normalized_title = normalize_text(title)
        release_type = envelope.release_type or "unknown"
        row = conn.execute(
            "SELECT id FROM releases WHERE identity_key = ?",
            (identity_key,),
        ).fetchone()
        params = (
            title,
            normalized_title,
            release_type,
            release_type,
            envelope.release_date,
            envelope.year,
            envelope.cover_art_id,
            identity_confidence,
            now,
        )
        if row is not None:
            conn.execute(
                """
                UPDATE releases
                SET title = ?, normalized_title = ?,
                    release_type = CASE WHEN ? != 'unknown' THEN ? ELSE release_type END,
                    release_date = COALESCE(?, release_date),
                    release_year = COALESCE(?, release_year),
                    cover_art_id = COALESCE(?, cover_art_id),
                    identity_confidence = ?, updated_at = ?
                WHERE id = ?
                """,
                (*params, row["id"]),
            )
            return int(row["id"])
        cursor = conn.execute(
            """
            INSERT INTO releases (
                title, normalized_title, release_type, release_date, release_year,
                cover_art_id, identity_key, identity_confidence, added_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                normalized_title,
                release_type,
                envelope.release_date,
                envelope.year,
                envelope.cover_art_id,
                identity_key,
                identity_confidence,
                now,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)

    def _insert_track_artist(
        self,
        conn: sqlite3.Connection,
        track_id: int,
        artist_id: int,
        credit: ArtistCredit,
        now: str,
    ) -> None:
        conn.execute(
            """
            INSERT OR IGNORE INTO track_artists (
                track_id, artist_id, role, position, credit_text, confidence, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                track_id,
                artist_id,
                credit.role,
                credit.position,
                credit.credit_text,
                credit.confidence,
                now,
            ),
        )

    def _insert_release_artist(
        self,
        conn: sqlite3.Connection,
        release_id: int,
        artist_id: int,
        credit: ArtistCredit,
        now: str,
    ) -> None:
        conn.execute(
            """
            INSERT OR IGNORE INTO release_artists (
                release_id, artist_id, role, position, credit_text, confidence, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                release_id,
                artist_id,
                credit.role,
                credit.position,
                credit.credit_text,
                credit.confidence,
                now,
            ),
        )

    def _refresh_release_artists(
        self,
        conn: sqlite3.Connection,
        release_id: int,
        *,
        explicit_credit: str | None,
        now: str,
    ) -> None:
        if clean_display_text(explicit_credit):
            conn.execute("DELETE FROM release_artists WHERE release_id = ?", (release_id,))
            for credit in parse_artist_credit(explicit_credit):
                explicit = replace(credit, confidence="explicit")
                artist_id = self._upsert_artist(conn, explicit.name, now)
                self._insert_release_artist(conn, release_id, artist_id, explicit, now)
            return

        existing_explicit = conn.execute(
            """
            SELECT 1 FROM release_artists
            WHERE release_id = ? AND confidence = 'explicit'
            LIMIT 1
            """,
            (release_id,),
        ).fetchone()
        if existing_explicit is not None:
            return

        # Count total tracks and per-artist track counts to find the dominant artist.
        # An artist present on ≥60% of tracks becomes the release artist;
        # otherwise fall back to "Various Artists".
        total_row = conn.execute(
            "SELECT COUNT(*) FROM release_tracks WHERE release_id = ?", (release_id,)
        ).fetchone()
        total_tracks = total_row[0] if total_row else 0

        rows = conn.execute(
            """
            SELECT
                ta.artist_id,
                ta.role,
                MIN(ta.credit_text) AS credit_text,
                MIN(COALESCE(rt.position, rt.track_id)) AS release_position,
                MIN(ta.position) AS artist_position,
                MIN(a.name) AS artist_name,
                COUNT(DISTINCT rt.track_id) AS track_count
            FROM release_tracks rt
            JOIN track_artists ta ON ta.track_id = rt.track_id
            JOIN artists a ON a.id = ta.artist_id
            WHERE rt.release_id = ? AND ta.role = 'primary'
            GROUP BY ta.artist_id, ta.role
            ORDER BY track_count DESC, release_position, artist_position, artist_name
            """,
            (release_id,),
        ).fetchall()
        conn.execute("DELETE FROM release_artists WHERE release_id = ?", (release_id,))

        if not rows:
            return

        dominant = rows[0]
        dominant_fraction = dominant["track_count"] / total_tracks if total_tracks else 0

        if dominant_fraction >= 0.6:
            # Single dominant artist covers most of the release
            credit = ArtistCredit(
                name=str(dominant["artist_name"]),
                role=str(dominant["role"]),
                position=0,
                credit_text=dominant["credit_text"],
                confidence="derived",
            )
            self._insert_release_artist(conn, release_id, int(dominant["artist_id"]), credit, now)
        else:
            # Too many different artists — use a synthetic "Various Artists" entry
            va_id = self._upsert_artist(conn, "Various Artists", now)
            credit = ArtistCredit(
                name="Various Artists",
                role="primary",
                position=0,
                credit_text=None,
                confidence="derived",
            )
            self._insert_release_artist(conn, release_id, va_id, credit, now)

    def _upsert_external_id(
        self,
        conn: sqlite3.Connection,
        *,
        provider: str,
        entity_type: str,
        entity_id: int,
        external_id: str,
        raw_json: str | None,
        synced_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO external_ids (
                provider, entity_type, entity_id, external_id, raw_json, synced_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, entity_type, external_id) DO UPDATE SET
                entity_id = excluded.entity_id,
                raw_json = excluded.raw_json,
                synced_at = excluded.synced_at
            """,
            (provider, entity_type, entity_id, external_id, raw_json, synced_at),
        )

    def _refresh_release_basics(
        self,
        conn: sqlite3.Connection,
        release_id: int,
        now: str,
    ) -> None:
        row = conn.execute(
            """
            SELECT COUNT(*) AS track_count, SUM(t.duration) AS duration, MAX(COALESCE(t.added_at, t.created_at)) AS added_at
            FROM release_tracks rt
            JOIN tracks t ON t.id = rt.track_id
            WHERE rt.release_id = ?
            """,
            (release_id,),
        ).fetchone()
        conn.execute(
            """
            UPDATE releases
            SET track_count = ?, duration = ?, added_at = COALESCE(?, added_at, created_at), updated_at = ?
            WHERE id = ?
            """,
            (
                int(row["track_count"] or 0),
                float(row["duration"]) if row["duration"] is not None else None,
                row["added_at"],
                now,
                release_id,
            ),
        )

    def _refresh_release_after_track_removal(
        self,
        conn: sqlite3.Connection,
        release_id: int,
        now: str,
    ) -> None:
        has_tracks = conn.execute(
            "SELECT 1 FROM release_tracks WHERE release_id = ? LIMIT 1",
            (release_id,),
        ).fetchone()
        if has_tracks is None:
            conn.execute("DELETE FROM release_artists WHERE release_id = ?", (release_id,))
        else:
            self._refresh_release_artists(conn, release_id, explicit_credit=None, now=now)
        self._refresh_release_basics(conn, release_id, now)

    def delete_embedding(self, track_id: int, model_name: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM embeddings WHERE track_id = ? AND model_name = ?",
                (track_id, model_name),
            )

    def save_feedback(
        self,
        seed_track_id: int,
        result_track_id: int,
        model_name: str,
        rating: int,
        note: str | None = None,
    ) -> None:
        if rating < 0 or rating > 3:
            raise ValueError("rating must be between 0 and 3")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO feedback (
                    seed_track_id, result_track_id, model_name, rating, note, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (seed_track_id, result_track_id, model_name, rating, note, utc_now()),
            )

    def feedback_for_seed(self, seed_track_id: int, model_name: str) -> dict[int, int]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT f.result_track_id, f.rating
                FROM feedback f
                JOIN (
                    SELECT result_track_id, MAX(created_at) AS created_at
                    FROM feedback
                    WHERE seed_track_id = ? AND model_name = ?
                    GROUP BY result_track_id
                ) latest
                  ON latest.result_track_id = f.result_track_id
                 AND latest.created_at = f.created_at
                WHERE f.seed_track_id = ? AND f.model_name = ?
                """,
                (seed_track_id, model_name, seed_track_id, model_name),
            ).fetchall()
        return {int(row["result_track_id"]): int(row["rating"]) for row in rows}

    def get_track(self, track_id: int) -> Track | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        return row_to_track(row) if row else None

    def get_tracks(self, track_ids: list[int]) -> dict[int, Track]:
        ids = list(dict.fromkeys(track_ids))
        if not ids:
            return {}
        placeholders = ", ".join("?" for _ in ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM tracks WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
        tracks = [row_to_track(row) for row in rows]
        return {int(track.id): track for track in tracks}

    def upsert_external_track(
        self,
        provider: str,
        external_id: str,
        track_id: int,
        raw_json: str | None = None,
        synced_at: str | None = None,
    ) -> ExternalTrack:
        provider = _require_external_value(provider, "provider")
        external_id = _require_external_value(external_id, "external_id")
        synced_at = synced_at or utc_now()
        with self.connect() as conn:
            track_exists = conn.execute(
                "SELECT 1 FROM tracks WHERE id = ?",
                (track_id,),
            ).fetchone()
            if track_exists is None:
                raise ValueError(f"Track not found: {track_id}")
            conn.execute(
                """
                DELETE FROM external_tracks
                WHERE provider = ? AND track_id = ? AND external_id != ?
                """,
                (provider, track_id, external_id),
            )
            conn.execute(
                """
                DELETE FROM external_ids
                WHERE provider = ?
                  AND entity_type = 'track'
                  AND entity_id = ?
                  AND external_id != ?
                """,
                (provider, track_id, external_id),
            )
            conn.execute(
                """
                INSERT INTO external_tracks (
                    provider, external_id, track_id, raw_json, synced_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(provider, external_id) DO UPDATE SET
                    track_id = excluded.track_id,
                    raw_json = excluded.raw_json,
                    synced_at = excluded.synced_at
                """,
                (provider, external_id, track_id, raw_json, synced_at),
            )
            self._upsert_external_id(
                conn,
                provider=provider,
                entity_type="track",
                entity_id=track_id,
                external_id=external_id,
                raw_json=raw_json,
                synced_at=synced_at,
            )
            row = conn.execute(
                """
                SELECT * FROM external_tracks
                WHERE provider = ? AND external_id = ?
                """,
                (provider, external_id),
            ).fetchone()
        return row_to_external_track(row)

    def get_external_track(self, provider: str, external_id: str) -> ExternalTrack | None:
        provider = _require_external_value(provider, "provider")
        external_id = _require_external_value(external_id, "external_id")
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM external_tracks
                WHERE provider = ? AND external_id = ?
                """,
                (provider, external_id),
            ).fetchone()
        return row_to_external_track(row) if row else None

    def get_track_by_external_id(self, provider: str, external_id: str) -> Track | None:
        provider = _require_external_value(provider, "provider")
        external_id = _require_external_value(external_id, "external_id")
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT t.*
                FROM tracks t
                JOIN external_tracks e ON e.track_id = t.id
                WHERE e.provider = ? AND e.external_id = ?
                """,
                (provider, external_id),
            ).fetchone()
        return row_to_track(row) if row else None

    def external_id_for_track(self, provider: str, track_id: int) -> str | None:
        provider = _require_external_value(provider, "provider")
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT external_id FROM external_tracks
                WHERE provider = ? AND track_id = ?
                """,
                (provider, track_id),
            ).fetchone()
        return str(row["external_id"]) if row else None

    def external_ids_for_tracks(self, provider: str, track_ids: list[int]) -> dict[int, str]:
        if not track_ids:
            return {}
        provider = _require_external_value(provider, "provider")
        placeholders = ",".join("?" for _ in track_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT track_id, external_id FROM external_tracks
                WHERE provider = ? AND track_id IN ({placeholders})
                """,
                [provider, *track_ids],
            ).fetchall()
        return {int(row["track_id"]): str(row["external_id"]) for row in rows}

    def list_external_tracks(self, provider: str | None = None) -> list[ExternalTrack]:
        params: list[object] = []
        where = ""
        if provider is not None:
            provider = _require_external_value(provider, "provider")
            where = "WHERE provider = ?"
            params.append(provider)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM external_tracks
                {where}
                ORDER BY provider, external_id
                """,
                params,
            ).fetchall()
        return [row_to_external_track(row) for row in rows]

    def count_external_tracks(self, provider: str | None = None) -> int:
        params: list[object] = []
        where = ""
        if provider is not None:
            provider = _require_external_value(provider, "provider")
            where = "WHERE provider = ?"
            params.append(provider)
        with self.connect() as conn:
            return int(
                conn.execute(
                    f"SELECT COUNT(*) FROM external_tracks {where}",
                    params,
                ).fetchone()[0]
            )

    def count_external_ids(self, provider: str | None = None, entity_type: str | None = None) -> int:
        where: list[str] = []
        params: list[object] = []
        if provider is not None:
            where.append("provider = ?")
            params.append(_require_external_value(provider, "provider"))
        if entity_type is not None:
            where.append("entity_type = ?")
            params.append(entity_type)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        with self.connect() as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM external_ids {where_sql}", params).fetchone()[0])

    def external_id_for_entity(self, provider: str, entity_type: str, entity_id: int) -> str | None:
        provider = _require_external_value(provider, "provider")
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT external_id
                FROM external_ids
                WHERE provider = ? AND entity_type = ? AND entity_id = ?
                ORDER BY synced_at DESC
                LIMIT 1
                """,
                (provider, entity_type, entity_id),
            ).fetchone()
        return str(row["external_id"]) if row else None

    def entity_id_for_external_id(self, provider: str, entity_type: str, external_id: str) -> int | None:
        provider = _require_external_value(provider, "provider")
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT entity_id
                FROM external_ids
                WHERE provider = ? AND entity_type = ? AND external_id = ?
                ORDER BY synced_at DESC
                LIMIT 1
                """,
                (provider, entity_type, external_id),
            ).fetchone()
        return int(row["entity_id"]) if row else None

    def update_artist_external_info(
        self,
        artist_id: int,
        *,
        image_url: str | None = None,
        bio: str | None = None,
    ) -> None:
        assignments = []
        params: list[object] = []
        if image_url:
            assignments.append("image_url = ?")
            params.append(image_url)
        if bio:
            assignments.append("bio = ?")
            params.append(bio)
        if not assignments:
            return
        assignments.append("updated_at = ?")
        params.append(utc_now())
        params.append(artist_id)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE artists SET {', '.join(assignments)} WHERE id = ?",
                params,
            )

    def artists_for_tracks(self, track_ids: list[int]) -> dict[int, list[Artist]]:
        with self.connect() as conn:
            return self._artists_for_tracks(conn, track_ids)

    def backfill_library_normalization(self) -> NormalizationStatus:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    t.*,
                    e.provider AS external_provider,
                    e.external_id AS external_id,
                    e.raw_json AS external_raw_json
                FROM tracks t
                LEFT JOIN external_tracks e ON e.track_id = t.id
                ORDER BY t.id
                """
            ).fetchall()
            for row in rows:
                envelope = envelope_from_track_row(row)
                if row["external_provider"] == "navidrome":
                    envelope = _envelope_from_track_with_external(row, envelope)
                self._upsert_normalized_track_sidecars(conn, int(row["id"]), envelope)
            return self._normalization_status(conn)

    def normalization_status(self) -> NormalizationStatus:
        with self.connect() as conn:
            return self._normalization_status(conn)

    def _normalization_status(self, conn: sqlite3.Connection) -> NormalizationStatus:
        return NormalizationStatus(
            total_tracks=int(conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]),
            tracks_with_release=int(
                conn.execute("SELECT COUNT(DISTINCT track_id) FROM release_tracks").fetchone()[0]
            ),
            tracks_with_artist=int(
                conn.execute("SELECT COUNT(DISTINCT track_id) FROM track_artists").fetchone()[0]
            ),
            releases=int(conn.execute("SELECT COUNT(*) FROM releases").fetchone()[0]),
            artists=int(conn.execute("SELECT COUNT(*) FROM artists").fetchone()[0]),
            orphan_releases=int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM releases r
                    WHERE NOT EXISTS (
                        SELECT 1 FROM release_tracks rt WHERE rt.release_id = r.id
                    )
                    """
                ).fetchone()[0]
            ),
            orphan_artists=int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM artists a
                    WHERE NOT EXISTS (
                        SELECT 1 FROM track_artists ta WHERE ta.artist_id = a.id
                    )
                      AND NOT EXISTS (
                        SELECT 1 FROM release_artists ra WHERE ra.artist_id = a.id
                    )
                    """
                ).fetchone()[0]
            ),
        )

    def get_artist(self, artist_id: int) -> ArtistSummaryRow | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM artists WHERE id = ?", (artist_id,)).fetchone()
            if row is None:
                return None
            stats = conn.execute(
                """
                SELECT
                    COUNT(DISTINCT ta.track_id) AS track_count,
                    COUNT(DISTINCT rt.release_id) AS release_count
                FROM artists a
                LEFT JOIN track_artists ta ON ta.artist_id = a.id
                LEFT JOIN release_tracks rt ON rt.track_id = ta.track_id
                WHERE a.id = ?
                """,
                (artist_id,),
            ).fetchone()
        return ArtistSummaryRow(
            artist=row_to_artist(row),
            track_count=int(stats["track_count"] or 0),
            release_count=int(stats["release_count"] or 0),
        )

    def get_release(self, release_id: int) -> ReleaseSummaryRow | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM releases WHERE id = ?", (release_id,)).fetchone()
            if row is None:
                return None
            artist_rows = conn.execute(
                """
                SELECT a.*
                FROM release_artists ra
                JOIN artists a ON a.id = ra.artist_id
                WHERE ra.release_id = ?
                ORDER BY ra.position, a.name
                """,
                (release_id,),
            ).fetchall()
        return ReleaseSummaryRow(row_to_release(row), [row_to_artist(item) for item in artist_rows])

    def list_release_tracks(self, release_id: int) -> list[ReleaseTrackRow]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT t.*, rt.disc_number, rt.track_number, rt.position
                FROM release_tracks rt
                JOIN tracks t ON t.id = rt.track_id
                WHERE rt.release_id = ?
                ORDER BY
                    rt.disc_number IS NULL,
                    rt.disc_number,
                    rt.track_number IS NULL,
                    rt.track_number,
                    rt.position,
                    t.id
                """,
                (release_id,),
            ).fetchall()
            artists_by_track = self._artists_for_tracks(conn, [int(row["id"]) for row in rows])
        return [
            ReleaseTrackRow(
                track=row_to_track(row),
                disc_number=int(row["disc_number"]) if row["disc_number"] is not None else None,
                track_number=int(row["track_number"]) if row["track_number"] is not None else None,
                position=int(row["position"]),
                artists=artists_by_track.get(int(row["id"]), []),
            )
            for row in rows
        ]

    def release_id_for_track(self, track_id: int) -> int | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT release_id
                FROM release_tracks
                WHERE track_id = ?
                ORDER BY position, release_id
                LIMIT 1
                """,
                (track_id,),
            ).fetchone()
        return int(row["release_id"]) if row else None

    def artist_ids_for_track(self, track_id: int) -> list[int]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT artist_id
                FROM track_artists
                WHERE track_id = ?
                ORDER BY
                    CASE role WHEN 'primary' THEN 0 ELSE 1 END,
                    position,
                    artist_id
                """,
                (track_id,),
            ).fetchall()
        return [int(row["artist_id"]) for row in rows]

    def artist_ids_for_release(self, release_id: int) -> list[int]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT artist_id
                FROM release_artists
                WHERE release_id = ?
                ORDER BY
                    CASE role WHEN 'primary' THEN 0 ELSE 1 END,
                    position,
                    artist_id
                """,
                (release_id,),
            ).fetchall()
        return [int(row["artist_id"]) for row in rows]

    def list_artist_track_ids(self, artist_id: int, *, limit: int = 100) -> list[int]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT t.id
                FROM track_artists ta
                JOIN tracks t ON t.id = ta.track_id
                LEFT JOIN release_tracks rt ON rt.track_id = t.id
                WHERE ta.artist_id = ?
                ORDER BY
                    CASE ta.role WHEN 'primary' THEN 0 ELSE 1 END,
                    COALESCE(rt.position, t.id),
                    t.id
                LIMIT ?
                """,
                (artist_id, limit),
            ).fetchall()
        return [int(row["id"]) for row in rows]

    def artist_discography(self, artist_id: int) -> dict[str, list[ReleaseSummaryRow]]:
        groups = {
            "albums": [],
            "eps": [],
            "singles": [],
            "compilations": [],
            "featured_in": [],
            "releases": [],
        }
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT r.*,
                    CASE WHEN ra.artist_id IS NULL THEN 1 ELSE 0 END AS featured_only
                FROM releases r
                JOIN release_tracks rt ON rt.release_id = r.id
                JOIN track_artists ta ON ta.track_id = rt.track_id
                LEFT JOIN release_artists ra
                  ON ra.release_id = r.id AND ra.artist_id = ?
                WHERE ta.artist_id = ?
                ORDER BY r.release_year IS NULL, r.release_year DESC, r.title
                """,
                (artist_id, artist_id),
            ).fetchall()
            artists_by_release = self._artists_for_releases(conn, [int(row["id"]) for row in rows])
        for row in rows:
            release = row_to_release(row)
            key = _discography_group_key(release.release_type, bool(row["featured_only"]))
            groups[key].append(
                ReleaseSummaryRow(release, artists_by_release.get(release.id, []))
            )
        return groups

    def related_discography_for_release(self, release_id: int, limit: int = 12) -> list[ReleaseSummaryRow]:
        context_artists = self.participating_artists_for_release(release_id)
        if not context_artists:
            return []
        artist_ids = [artist.id for artist in context_artists]
        placeholders = ",".join("?" for _id in artist_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT r.*, ra.artist_id AS context_artist_id
                FROM releases r
                JOIN release_artists ra ON ra.release_id = r.id
                WHERE ra.artist_id IN ({placeholders})
                  AND r.id != ?
                ORDER BY r.release_year IS NULL, r.release_year DESC, r.title
                """,
                (*artist_ids, release_id),
            ).fetchall()
            selected_ids: list[int] = []
            seen_release_ids: set[int] = set()
            rows_by_artist: dict[int, list[sqlite3.Row]] = {}
            for row in rows:
                rows_by_artist.setdefault(int(row["context_artist_id"]), []).append(row)
            while len(selected_ids) < limit:
                added = False
                for artist_id in artist_ids:
                    candidates = rows_by_artist.get(artist_id, [])
                    while candidates:
                        row = candidates.pop(0)
                        candidate_id = int(row["id"])
                        if candidate_id in seen_release_ids:
                            continue
                        seen_release_ids.add(candidate_id)
                        selected_ids.append(candidate_id)
                        added = True
                        break
                    if len(selected_ids) >= limit:
                        break
                if not added:
                    break
            rows_by_id = {int(row["id"]): row for row in rows}
            selected_rows = [rows_by_id[release_id] for release_id in selected_ids]
            artists_by_release = self._artists_for_releases(conn, selected_ids)
        return [
            ReleaseSummaryRow(row_to_release(row), artists_by_release.get(int(row["id"]), []))
            for row in selected_rows
        ]

    def participating_artists_for_release(self, release_id: int) -> list[Artist]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT a.*,
                    MIN(COALESCE(ra.position, ta.position, 999999)) AS sort_position
                FROM artists a
                LEFT JOIN release_artists ra
                  ON ra.artist_id = a.id AND ra.release_id = ?
                LEFT JOIN release_tracks rt
                  ON rt.release_id = ?
                LEFT JOIN track_artists ta
                  ON ta.artist_id = a.id AND ta.track_id = rt.track_id
                WHERE ra.artist_id IS NOT NULL OR ta.artist_id IS NOT NULL
                GROUP BY a.id
                ORDER BY sort_position, a.name
                """,
                (release_id, release_id),
            ).fetchall()
        return [row_to_artist(row) for row in rows]

    def search_entities(
        self,
        query: str,
        *,
        entity_type: str = "all",
        limit: int = 8,
        offset: int = 0,
    ) -> dict[str, object]:
        cleaned = " ".join(query.strip().split())
        empty = {
            "artists": {"items": [], "total": 0},
            "releases": {"items": [], "total": 0},
            "tracks": {"items": [], "total": 0},
        }
        if not cleaned:
            return empty
        like = f"%{cleaned}%"
        normalized_like = f"%{normalize_text(cleaned)}%"
        with self.connect() as conn:
            artists = []
            artist_total = 0
            if entity_type in {"all", "artist"}:
                artist_total = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM artists WHERE normalized_name LIKE ?",
                        (normalized_like,),
                    ).fetchone()[0]
                )
                artists = [
                    ArtistSummaryRow(row_to_artist(row), int(row["track_count"] or 0), int(row["release_count"] or 0))
                    for row in conn.execute(
                        """
                        SELECT a.*,
                            COUNT(DISTINCT ta.track_id) AS track_count,
                            COUNT(DISTINCT rt.release_id) AS release_count
                        FROM artists a
                        LEFT JOIN track_artists ta ON ta.artist_id = a.id
                        LEFT JOIN release_tracks rt ON rt.track_id = ta.track_id
                        WHERE a.normalized_name LIKE ?
                        GROUP BY a.id
                        ORDER BY CASE WHEN a.normalized_name = ? THEN 0 ELSE 1 END, a.name
                        LIMIT ? OFFSET ?
                        """,
                        (normalized_like, normalize_text(cleaned), limit, offset),
                    ).fetchall()
                ]

            releases = []
            release_total = 0
            if entity_type in {"all", "release"}:
                release_total = int(
                    conn.execute(
                        """
                        SELECT COUNT(DISTINCT r.id)
                        FROM releases r
                        LEFT JOIN release_artists ra ON ra.release_id = r.id
                        LEFT JOIN artists a ON a.id = ra.artist_id
                        WHERE r.normalized_title LIKE ? OR a.normalized_name LIKE ?
                        """,
                        (normalized_like, normalized_like),
                    ).fetchone()[0]
                )
                release_rows = conn.execute(
                    """
                    SELECT DISTINCT r.*
                    FROM releases r
                    LEFT JOIN release_artists ra ON ra.release_id = r.id
                    LEFT JOIN artists a ON a.id = ra.artist_id
                    WHERE r.normalized_title LIKE ? OR a.normalized_name LIKE ?
                    ORDER BY CASE WHEN r.normalized_title = ? THEN 0 ELSE 1 END, r.title
                    LIMIT ? OFFSET ?
                    """,
                    (normalized_like, normalized_like, normalize_text(cleaned), limit, offset),
                ).fetchall()
                artists_by_release = self._artists_for_releases(conn, [int(row["id"]) for row in release_rows])
                releases = [
                    ReleaseSummaryRow(row_to_release(row), artists_by_release.get(int(row["id"]), []))
                    for row in release_rows
                ]

            tracks = []
            track_total = 0
            if entity_type in {"all", "track"}:
                track_total = int(
                    conn.execute(
                        """
                        SELECT COUNT(DISTINCT t.id)
                        FROM tracks t
                        LEFT JOIN release_tracks rt ON rt.track_id = t.id
                        LEFT JOIN releases r ON r.id = rt.release_id
                        LEFT JOIN track_artists ta ON ta.track_id = t.id
                        LEFT JOIN artists a ON a.id = ta.artist_id
                        WHERE t.title LIKE ? OR t.artist LIKE ? OR t.album LIKE ?
                           OR r.normalized_title LIKE ? OR a.normalized_name LIKE ?
                        """,
                        (like, like, like, normalized_like, normalized_like),
                    ).fetchone()[0]
                )
                track_rows = conn.execute(
                    """
                    SELECT DISTINCT t.*
                    FROM tracks t
                    LEFT JOIN release_tracks rt ON rt.track_id = t.id
                    LEFT JOIN releases r ON r.id = rt.release_id
                    LEFT JOIN track_artists ta ON ta.track_id = t.id
                    LEFT JOIN artists a ON a.id = ta.artist_id
                    WHERE t.title LIKE ? OR t.artist LIKE ? OR t.album LIKE ?
                       OR r.normalized_title LIKE ? OR a.normalized_name LIKE ?
                    ORDER BY t.artist, t.album, t.title, t.id
                    LIMIT ? OFFSET ?
                    """,
                    (like, like, like, normalized_like, normalized_like, limit, offset),
                ).fetchall()
                tracks = [row_to_track(row) for row in track_rows]
        return {
            "artists": {"items": artists, "total": artist_total},
            "releases": {"items": releases, "total": release_total},
            "tracks": {"items": tracks, "total": track_total},
        }

    def _artists_for_tracks(
        self,
        conn: sqlite3.Connection,
        track_ids: list[int],
    ) -> dict[int, list[Artist]]:
        if not track_ids:
            return {}
        placeholders = ",".join("?" for _id in track_ids)
        rows = conn.execute(
            f"""
            SELECT ta.track_id, a.*
            FROM track_artists ta
            JOIN artists a ON a.id = ta.artist_id
            WHERE ta.track_id IN ({placeholders})
            ORDER BY ta.track_id, ta.position, a.name
            """,
            track_ids,
        ).fetchall()
        grouped: dict[int, list[Artist]] = {}
        for row in rows:
            grouped.setdefault(int(row["track_id"]), []).append(row_to_artist(row))
        return grouped

    def _artists_for_releases(
        self,
        conn: sqlite3.Connection,
        release_ids: list[int],
    ) -> dict[int, list[Artist]]:
        if not release_ids:
            return {}
        placeholders = ",".join("?" for _id in release_ids)
        rows = conn.execute(
            f"""
            SELECT ra.release_id, a.*
            FROM release_artists ra
            JOIN artists a ON a.id = ra.artist_id
            WHERE ra.release_id IN ({placeholders})
            ORDER BY ra.release_id, ra.position, a.name
            """,
            release_ids,
        ).fetchall()
        grouped: dict[int, list[Artist]] = {}
        for row in rows:
            grouped.setdefault(int(row["release_id"]), []).append(row_to_artist(row))
        return grouped

