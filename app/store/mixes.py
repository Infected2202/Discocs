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
    InstantMixRequestRecord,
    LATE_SKIP_FRACTION,
    MEANINGFUL_LISTEN_FRACTION,
    MEANINGFUL_LISTEN_SECONDS,
    NormalizationStatus,
    Playlist,
    PlaylistItem,
    PLAYLIST_KINDS,
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
_SELECT_GENERATED_MIX_BY_ID = "SELECT * FROM generated_mixes WHERE id = ?"
_SELECT_PLAYLIST_BY_ID = "SELECT * FROM playlists WHERE id = ?"
_INSERT_PLAYLIST_ITEM = (
    "INSERT INTO playlist_items (playlist_id, position, track_id, created_at) VALUES (?, ?, ?, ?)"
)
_UNSET: object = object()

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
                _SELECT_GENERATED_MIX_BY_ID,
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
                _SELECT_GENERATED_MIX_BY_ID,
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
                _SELECT_GENERATED_MIX_BY_ID,
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

    def save_generated_mix_as_playlist(
        self,
        mix_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
    ) -> GeneratedMix | None:
        now = utc_now()
        playlist_title = (title or "").strip()
        with self.connect() as conn:
            mix = conn.execute(
                "SELECT * FROM generated_mixes WHERE id = ? AND status != 'archived'",
                (mix_id,),
            ).fetchone()
            if mix is None:
                return None
            if not playlist_title:
                playlist_title = str(mix["title"])
            existing_playlist_id = mix["saved_playlist_id"]
            source = {
                "source_type": "generated_mix",
                "generated_mix_id": mix_id,
                "mix_type": mix["mix_type"],
            }
            if existing_playlist_id is None:
                cursor = conn.execute(
                    """
                    INSERT INTO playlists (title, kind, description, source_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (playlist_title, "saved_mix", description, _json_dumps(source), now, now),
                )
                playlist_id = int(cursor.lastrowid)
            else:
                playlist_id = int(existing_playlist_id)
                conn.execute(
                    """
                    UPDATE playlists
                    SET title = ?, kind = ?, description = ?, source_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (playlist_title, "saved_mix", description, _json_dumps(source), now, playlist_id),
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
                _SELECT_GENERATED_MIX_BY_ID,
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

    def create_playlist(
        self,
        *,
        title: str,
        description: str | None = None,
        kind: str = "manual",
        source: dict[str, object] | None = None,
        track_ids: list[int] | None = None,
    ) -> Playlist:
        _require_choice(kind, PLAYLIST_KINDS, "kind")
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("Playlist title must not be empty")
        now = utc_now()
        ids = [int(track_id) for track_id in (track_ids or [])]
        with self.connect() as conn:
            _validate_track_ids(conn, ids)
            cursor = conn.execute(
                """
                INSERT INTO playlists (title, kind, description, source_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (clean_title, kind, description, _json_dumps(source), now, now),
            )
            playlist_id = int(cursor.lastrowid)
            position = 0
            seen: set[int] = set()
            for track_id in ids:
                if track_id in seen:
                    continue
                seen.add(track_id)
                conn.execute(_INSERT_PLAYLIST_ITEM, (playlist_id, position, track_id, now))
                position += 1
            row = conn.execute(_SELECT_PLAYLIST_BY_ID, (playlist_id,)).fetchone()
        return row_to_playlist(row)

    def update_playlist(
        self,
        playlist_id: int,
        *,
        title: str | None = None,
        description: str | None | object = _UNSET,
    ) -> Playlist | None:
        sets: list[str] = []
        params: list[object] = []
        if title is not None:
            clean_title = title.strip()
            if not clean_title:
                raise ValueError("Playlist title must not be empty")
            sets.append("title = ?")
            params.append(clean_title)
        if description is not _UNSET:
            sets.append("description = ?")
            params.append(description)
        with self.connect() as conn:
            if sets:
                sets.append("updated_at = ?")
                params.append(utc_now())
                cursor = conn.execute(
                    f"UPDATE playlists SET {', '.join(sets)} WHERE id = ?",
                    [*params, playlist_id],
                )
                if cursor.rowcount == 0:
                    return None
            row = conn.execute(_SELECT_PLAYLIST_BY_ID, (playlist_id,)).fetchone()
        return row_to_playlist(row) if row else None

    def delete_playlist(self, playlist_id: int) -> bool:
        now = utc_now()
        with self.connect() as conn:
            # Saved mixes point at their playlist; deleting the playlist makes the
            # mix saveable again instead of leaving a dangling reference.
            conn.execute(
                """
                UPDATE generated_mixes
                SET status = CASE WHEN status = 'saved' THEN 'active' ELSE status END,
                    saved_playlist_id = NULL,
                    updated_at = ?
                WHERE saved_playlist_id = ?
                """,
                (now, playlist_id),
            )
            cursor = conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
            return cursor.rowcount > 0

    def list_playlists(self, *, limit: int = 50, offset: int = 0) -> list[Playlist]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM playlists
                ORDER BY updated_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (int(limit), int(offset)),
            ).fetchall()
        return [row_to_playlist(row) for row in rows]

    def count_playlists(self) -> int:
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM playlists").fetchone()[0])

    def playlist_track_counts(self, playlist_ids: list[int]) -> dict[int, int]:
        if not playlist_ids:
            return {}
        unique_ids = sorted({int(playlist_id) for playlist_id in playlist_ids})
        placeholders = ", ".join("?" for _ in unique_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT playlist_id, COUNT(*) AS track_count
                FROM playlist_items
                WHERE playlist_id IN ({placeholders})
                GROUP BY playlist_id
                """,
                unique_ids,
            ).fetchall()
        return {int(row["playlist_id"]): int(row["track_count"]) for row in rows}

    def playlist_track_ids(self, playlist_id: int) -> list[int]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT track_id FROM playlist_items
                WHERE playlist_id = ?
                ORDER BY position
                """,
                (playlist_id,),
            ).fetchall()
        return [int(row["track_id"]) for row in rows]

    def add_playlist_tracks(self, playlist_id: int, track_ids: list[int]) -> int:
        now = utc_now()
        ids = [int(track_id) for track_id in track_ids]
        with self.connect() as conn:
            if conn.execute(_SELECT_PLAYLIST_BY_ID, (playlist_id,)).fetchone() is None:
                raise ValueError(f"Playlist not found: {playlist_id}")
            _validate_track_ids(conn, ids)
            existing = {
                int(row["track_id"])
                for row in conn.execute(
                    "SELECT track_id FROM playlist_items WHERE playlist_id = ?",
                    (playlist_id,),
                )
            }
            position = int(
                conn.execute(
                    "SELECT COALESCE(MAX(position) + 1, 0) FROM playlist_items WHERE playlist_id = ?",
                    (playlist_id,),
                ).fetchone()[0]
            )
            added = 0
            for track_id in ids:
                if track_id in existing:
                    continue
                existing.add(track_id)
                conn.execute(_INSERT_PLAYLIST_ITEM, (playlist_id, position, track_id, now))
                position += 1
                added += 1
            if added:
                conn.execute("UPDATE playlists SET updated_at = ? WHERE id = ?", (now, playlist_id))
        return added

    def remove_playlist_tracks(self, playlist_id: int, track_ids: list[int]) -> int:
        now = utc_now()
        ids = sorted({int(track_id) for track_id in track_ids})
        if not ids:
            return 0
        placeholders = ", ".join("?" for _ in ids)
        with self.connect() as conn:
            cursor = conn.execute(
                f"DELETE FROM playlist_items WHERE playlist_id = ? AND track_id IN ({placeholders})",
                [playlist_id, *ids],
            )
            removed = int(cursor.rowcount)
            if removed:
                self._repack_playlist_positions(conn, playlist_id)
                conn.execute("UPDATE playlists SET updated_at = ? WHERE id = ?", (now, playlist_id))
        return removed

    def _repack_playlist_positions(self, conn: sqlite3.Connection, playlist_id: int) -> None:
        # Re-insert to keep positions contiguous without tripping the
        # (playlist_id, position) primary key during shifts.
        rows = conn.execute(
            """
            SELECT track_id, created_at FROM playlist_items
            WHERE playlist_id = ?
            ORDER BY position
            """,
            (playlist_id,),
        ).fetchall()
        conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
        for position, row in enumerate(rows):
            conn.execute(
                _INSERT_PLAYLIST_ITEM,
                (playlist_id, position, int(row["track_id"]), str(row["created_at"])),
            )

    def set_playlist_cover_path(self, playlist_id: int, cover_path: str | None) -> Playlist | None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE playlists SET cover_path = ? WHERE id = ?",
                (cover_path, playlist_id),
            )
            row = conn.execute(_SELECT_PLAYLIST_BY_ID, (playlist_id,)).fetchone()
        return row_to_playlist(row) if row else None

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

    def record_instant_mix_request(self, record: InstantMixRequestRecord) -> InstantMixRequest:
        created_at = record.created_at or utc_now()
        params_json = json.dumps(
            {
                "requested_model": record.params.requested_model,
                "effective_model": record.params.effective_model,
                "requested_count": record.params.requested_count,
                "requested_max_per_artist": record.params.requested_max_per_artist,
                "requested_exclude_same_album": record.params.requested_exclude_same_album,
                "effective_count": record.params.effective_count,
                "max_per_artist": record.params.max_per_artist,
                "exclude_same_album": record.params.exclude_same_album,
                "count_collaboration_artists": record.params.count_collaboration_artists,
                "min_similarity": record.params.min_similarity,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        results_json = json.dumps(list(record.results), ensure_ascii=True)
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
                    record.request_id,
                    record.provider,
                    record.seed_item_id,
                    record.seed_track_id,
                    record.model_name,
                    record.params.requested_count,
                    record.params.effective_count,
                    record.params.max_per_artist,
                    1 if record.params.exclude_same_album else 0,
                    record.params.min_similarity,
                    record.status,
                    len(record.results),
                    record.skipped_without_external_id,
                    record.duration_ms,
                    record.error,
                    params_json,
                    results_json,
                    created_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM instant_mix_requests NOT INDEXED WHERE id = ?",
                (record.request_id,),
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

