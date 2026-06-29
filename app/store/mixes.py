"""Store Mixes domain: generated mixes, instant mixes, playlists.

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
    _json_dumps,
    _optional_float,
    _require_choice,
    _validate_track_ids,
    row_to_generated_mix,
    row_to_generated_mix_item,
    row_to_instant_mix_request,
    row_to_playlist,
    row_to_playlist_item,
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

class MixesStoreMixin:
    def save_generated_mix(
        self,
        *,
        mix_id: str,
        title: str,
        mix_type: str,
        status: str = "active",
        anchor: dict[str, object] | None = None,
        settings: dict[str, object] | None = None,
        score_summary: dict[str, object] | None = None,
        items: list[dict[str, object]] | None = None,
        cover_path: str | None = None,
        expires_at: str | None = None,
        saved_playlist_id: int | None = None,
    ) -> GeneratedMix:
        _require_choice(mix_type, GENERATED_MIX_TYPES, "mix_type")
        _require_choice(status, GENERATED_MIX_STATUSES, "status")
        now = utc_now()
        mix_items = items or []
        with self.connect() as conn:
            _validate_track_ids(conn, [int(item["track_id"]) for item in mix_items])
            existing = conn.execute(
                "SELECT created_at FROM generated_mixes WHERE id = ?",
                (mix_id,),
            ).fetchone()
            created_at = str(existing["created_at"]) if existing else now
            conn.execute(
                """
                INSERT INTO generated_mixes (
                    id, title, mix_type, status, cover_path, anchor_json, settings_json,
                    score_summary_json, created_at, updated_at, expires_at,
                    saved_playlist_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    mix_type = excluded.mix_type,
                    status = excluded.status,
                    cover_path = COALESCE(excluded.cover_path, generated_mixes.cover_path),
                    anchor_json = excluded.anchor_json,
                    settings_json = excluded.settings_json,
                    score_summary_json = excluded.score_summary_json,
                    updated_at = excluded.updated_at,
                    expires_at = excluded.expires_at,
                    saved_playlist_id = excluded.saved_playlist_id
                """,
                (
                    mix_id,
                    title,
                    mix_type,
                    status,
                    cover_path,
                    _json_dumps(anchor),
                    _json_dumps(settings),
                    _json_dumps(score_summary),
                    created_at,
                    now,
                    expires_at,
                    saved_playlist_id,
                ),
            )
            conn.execute("DELETE FROM generated_mix_items WHERE mix_id = ?", (mix_id,))
            for index, item in enumerate(mix_items):
                conn.execute(
                    """
                    INSERT INTO generated_mix_items (
                        mix_id, position, track_id, score, score_breakdown_json,
                        reason_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        mix_id,
                        int(item.get("position", index)),
                        int(item["track_id"]),
                        _optional_float(item.get("score")),
                        _json_dumps(item.get("score_breakdown"))
                        if isinstance(item.get("score_breakdown"), dict)
                        else item.get("score_breakdown_json"),
                        _json_dumps(item.get("reason"))
                        if isinstance(item.get("reason"), dict)
                        else item.get("reason_json"),
                        now,
                    ),
                )
            row = conn.execute(
                "SELECT * FROM generated_mixes WHERE id = ?",
                (mix_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError(f"Generated mix not found after save: {mix_id}")
        return row_to_generated_mix(row)

    def set_generated_mix_cover_path(self, mix_id: str, cover_path: str | None) -> GeneratedMix | None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE generated_mixes SET cover_path = ?, updated_at = ? WHERE id = ?",
                (cover_path, utc_now(), mix_id),
            )
            row = conn.execute(
                "SELECT * FROM generated_mixes WHERE id = ?",
                (mix_id,),
            ).fetchone()
        return row_to_generated_mix(row) if row else None

    def list_generated_mixes(
        self,
        *,
        statuses: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[GeneratedMix]:
        status_values = statuses or ["active", "saved"]
        for status in status_values:
            _require_choice(status, GENERATED_MIX_STATUSES, "status")
        placeholders = ", ".join("?" for _ in status_values)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM generated_mixes
                WHERE status IN ({placeholders})
                ORDER BY
                    CASE
                        WHEN id LIKE 'mix-%' THEN substr(id, 5, 20)
                        ELSE updated_at
                    END DESC,
                    CASE
                        WHEN id LIKE 'mix-%' THEN CAST(substr(substr(id, 26), 1, instr(substr(id, 26), '-') - 1) AS INTEGER)
                        ELSE 0
                    END ASC,
                    updated_at DESC,
                    created_at DESC,
                    id
                LIMIT ? OFFSET ?
                """,
                [*status_values, int(limit), int(offset)],
            ).fetchall()
        return [row_to_generated_mix(row) for row in rows]

    def count_generated_mixes(self, statuses: list[str] | None = None) -> int:
        status_values = statuses or ["active", "saved"]
        for status in status_values:
            _require_choice(status, GENERATED_MIX_STATUSES, "status")
        placeholders = ", ".join("?" for _ in status_values)
        with self.connect() as conn:
            return int(
                conn.execute(
                    f"SELECT COUNT(*) FROM generated_mixes WHERE status IN ({placeholders})",
                    status_values,
                ).fetchone()[0]
            )

    def get_generated_mix(self, mix_id: str) -> GeneratedMix | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM generated_mixes WHERE id = ?",
                (mix_id,),
            ).fetchone()
        return row_to_generated_mix(row) if row else None

    def list_generated_mix_items(self, mix_id: str) -> list[GeneratedMixItem]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM generated_mix_items
                WHERE mix_id = ?
                ORDER BY position
                """,
                (mix_id,),
            ).fetchall()
        return [row_to_generated_mix_item(row) for row in rows]

    def save_generated_mix_as_playlist(self, mix_id: str) -> GeneratedMix | None:
        now = utc_now()
        with self.connect() as conn:
            mix = conn.execute(
                "SELECT * FROM generated_mixes WHERE id = ? AND status != 'archived'",
                (mix_id,),
            ).fetchone()
            if mix is None:
                return None
            existing_playlist_id = mix["saved_playlist_id"]
            source = {
                "source_type": "generated_mix",
                "generated_mix_id": mix_id,
                "mix_type": mix["mix_type"],
            }
            if existing_playlist_id is None:
                cursor = conn.execute(
                    """
                    INSERT INTO playlists (title, kind, source_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (mix["title"], "saved_mix", _json_dumps(source), now, now),
                )
                playlist_id = int(cursor.lastrowid)
            else:
                playlist_id = int(existing_playlist_id)
                conn.execute(
                    """
                    UPDATE playlists
                    SET title = ?, kind = ?, source_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (mix["title"], "saved_mix", _json_dumps(source), now, playlist_id),
                )
            conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
            items = conn.execute(
                """
                SELECT track_id
                FROM generated_mix_items
                WHERE mix_id = ?
                ORDER BY position
                """,
                (mix_id,),
            ).fetchall()
            for position, item in enumerate(items):
                conn.execute(
                    """
                    INSERT INTO playlist_items (playlist_id, position, track_id, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (playlist_id, position, int(item["track_id"]), now),
                )
            conn.execute(
                """
                UPDATE generated_mixes
                SET status = 'saved', saved_playlist_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (playlist_id, now, mix_id),
            )
            row = conn.execute(
                "SELECT * FROM generated_mixes WHERE id = ?",
                (mix_id,),
            ).fetchone()
        return row_to_generated_mix(row) if row else None

    def get_playlist(self, playlist_id: int) -> Playlist | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
        return row_to_playlist(row) if row else None

    def list_playlist_items(self, playlist_id: int) -> list[PlaylistItem]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM playlist_items
                WHERE playlist_id = ?
                ORDER BY position
                """,
                (playlist_id,),
            ).fetchall()
        return [row_to_playlist_item(row) for row in rows]

    def mark_generated_mixes_stale(self, *, mix_type: str | None = None) -> int:
        params: list[object] = [utc_now()]
        where = "status = 'active'"
        if mix_type is not None:
            _require_choice(mix_type, GENERATED_MIX_TYPES, "mix_type")
            where += " AND mix_type = ?"
            params.append(mix_type)
        with self.connect() as conn:
            cursor = conn.execute(
                f"UPDATE generated_mixes SET status = 'stale', updated_at = ? WHERE {where}",
                params,
            )
            return int(cursor.rowcount)

    def recompute_user_preferences(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM user_track_preferences")
            conn.execute("DELETE FROM user_release_preferences")
            conn.execute("DELETE FROM user_artist_preferences")
            rows = conn.execute(
                """
                SELECT * FROM playback_events
                ORDER BY created_at, id
                """
            ).fetchall()
            for row in rows:
                self._apply_playback_event_preferences(conn, row)

    def record_instant_mix_request(
        self,
        request_id: str,
        provider: str,
        seed_item_id: str,
        seed_track_id: int | None,
        model_name: str,
        requested_count: int | None,
        effective_count: int,
        max_per_artist: int,
        exclude_same_album: bool,
        min_similarity: float | None,
        status: str,
        result_count: int,
        skipped_without_external_id: int,
        duration_ms: float | None,
        error: str | None,
        params_json: str,
        results_json: str,
        created_at: str | None = None,
    ) -> InstantMixRequest:
        created_at = created_at or utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO instant_mix_requests (
                    id, provider, seed_item_id, seed_track_id, model_name,
                    requested_count, effective_count, max_per_artist, exclude_same_album,
                    min_similarity, status, result_count, skipped_without_external_id,
                    duration_ms, error, params_json, results_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    provider,
                    seed_item_id,
                    seed_track_id,
                    model_name,
                    requested_count,
                    effective_count,
                    max_per_artist,
                    1 if exclude_same_album else 0,
                    min_similarity,
                    status,
                    result_count,
                    skipped_without_external_id,
                    duration_ms,
                    error,
                    params_json,
                    results_json,
                    created_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM instant_mix_requests NOT INDEXED WHERE id = ?",
                (request_id,),
            ).fetchone()
        return row_to_instant_mix_request(row)

    def list_instant_mix_requests(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[InstantMixRequest]:
        with self.connect() as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM instant_mix_requests NOT INDEXED
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                ).fetchall()
            except sqlite3.DatabaseError:
                logger.exception("Falling back to rowid scan for instant_mix_requests")
                rows = self._list_instant_mix_requests_by_rowid(conn, limit=limit, offset=offset)
        return [row_to_instant_mix_request(row) for row in rows]

    def _list_instant_mix_requests_by_rowid(
        self,
        conn: sqlite3.Connection,
        *,
        limit: int,
        offset: int,
    ) -> list[sqlite3.Row]:
        bounds = conn.execute(
            "SELECT MIN(rowid), MAX(rowid) FROM instant_mix_requests NOT INDEXED"
        ).fetchone()
        if bounds is None or bounds[0] is None or bounds[1] is None:
            return []
        rows: list[sqlite3.Row] = []
        bad_rowids: list[int] = []
        for rowid in range(int(bounds[0]), int(bounds[1]) + 1):
            try:
                row = conn.execute(
                    "SELECT * FROM instant_mix_requests NOT INDEXED WHERE rowid = ?",
                    (rowid,),
                ).fetchone()
            except sqlite3.DatabaseError:
                bad_rowids.append(rowid)
                continue
            if row is not None:
                rows.append(row)
        if bad_rowids:
            logger.warning("Skipped unreadable instant_mix_requests rowids=%s", bad_rowids)
        rows.sort(key=lambda row: (str(row["created_at"] or ""), str(row["id"] or "")), reverse=True)
        return rows[offset : offset + limit]

    def get_instant_mix_request(self, request_id: str) -> InstantMixRequest | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM instant_mix_requests NOT INDEXED WHERE id = ?",
                (request_id,),
            ).fetchone()
        return row_to_instant_mix_request(row) if row else None

