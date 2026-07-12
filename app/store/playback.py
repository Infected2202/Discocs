"""Store Playback domain: sessions, queue, events, preferences.

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
    _artist_ids_for_event,
    _artist_ids_for_track,
    _json_dumps,
    _json_loads,
    _normalized_play_fraction,
    _optional_float,
    _optional_int,
    _playback_session_exists,
    _preference_delta_dict,
    _primary_artist_id_for_track,
    _release_id_for_track,
    _require_choice,
    _validate_track_ids,
    playback_event_is_completion,
    playback_event_is_early_skip,
    playback_skip_score_delta,
    row_to_playback_event,
    row_to_playback_session,
    row_to_queue_item,
    row_to_user_artist_preference,
    row_to_user_release_preference,
    row_to_user_track_preference,
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
    PlaybackEventCreate,
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
_TOUCH_PLAYBACK_SESSION = "UPDATE playback_sessions SET updated_at = ? WHERE id = ?"

class PlaybackStoreMixin:
    def create_playback_session(
        self,
        *,
        source_type: str,
        source_id: int | None = None,
        source_label: str | None = None,
        mode: str = "linear",
        status: str = "active",
        track_ids: list[int] | None = None,
        autoplay_enabled: bool = False,
        shuffle_enabled: bool = False,
        repeat_mode: str = "off",
        settings: dict[str, object] | None = None,
        state: dict[str, object] | None = None,
        session_id: str | None = None,
    ) -> tuple[PlaybackSession, list[QueueItem]]:
        user_id = self.require_user_id()
        _require_choice(source_type, PLAYBACK_SOURCE_TYPES, "source_type")
        _require_choice(mode, PLAYBACK_MODES, "mode")
        _require_choice(status, PLAYBACK_SESSION_STATUSES, "status")
        _require_choice(repeat_mode, PLAYBACK_REPEAT_MODES, "repeat_mode")
        now = utc_now()
        session_id = session_id or str(uuid4())
        settings_json = _json_dumps(settings)
        state_json = _json_dumps(state)
        ended_at = now if status == "ended" else None
        initial_track_ids = [int(track_id) for track_id in (track_ids or [])]
        with self.connect() as conn:
            _validate_track_ids(conn, initial_track_ids)
            conn.execute(
                """
                INSERT INTO playback_sessions (
                    id, user_id, source_type, source_id, source_label, mode, status,
                    autoplay_enabled, shuffle_enabled, repeat_mode, started_at,
                    updated_at, ended_at, settings_json, state_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    user_id,
                    source_type,
                    source_id,
                    source_label,
                    mode,
                    status,
                    1 if autoplay_enabled else 0,
                    1 if shuffle_enabled else 0,
                    repeat_mode,
                    now,
                    now,
                    ended_at,
                    settings_json,
                    state_json,
                ),
            )
        queue = self.replace_queue_items(
            session_id,
            [
                {
                    "track_id": track_id,
                    "origin": "source",
                    "source_type": source_type,
                    "source_id": source_id,
                }
                for track_id in initial_track_ids
            ],
        )
        session = self.get_playback_session(session_id)
        if session is None:
            raise RuntimeError(f"Playback session not found after create: {session_id}")
        return session, queue

    def get_playback_session(self, session_id: str) -> PlaybackSession | None:
        user_id = self.require_user_id()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM playback_sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            ).fetchone()
        return row_to_playback_session(row) if row else None

    def update_playback_session(
        self,
        session_id: str,
        *,
        status: str | None = None,
        current_track_id: int | None = None,
        current_queue_item_id: str | None = None,
        clear_current_track: bool = False,
        clear_current_queue_item: bool = False,
        autoplay_enabled: bool | None = None,
        shuffle_enabled: bool | None = None,
        repeat_mode: str | None = None,
        settings: dict[str, object] | None = None,
        state: dict[str, object] | None = None,
    ) -> PlaybackSession | None:
        user_id = self.require_user_id()
        updates: list[str] = []
        params: list[object] = []
        if status is not None:
            _require_choice(status, PLAYBACK_SESSION_STATUSES, "status")
            updates.append("status = ?")
            params.append(status)
            updates.append("ended_at = ?")
            params.append(utc_now() if status == "ended" else None)
        if clear_current_track:
            updates.append("current_track_id = ?")
            params.append(None)
        elif current_track_id is not None:
            updates.append("current_track_id = ?")
            params.append(current_track_id)
        if clear_current_queue_item:
            updates.append("current_queue_item_id = ?")
            params.append(None)
        elif current_queue_item_id is not None:
            updates.append("current_queue_item_id = ?")
            params.append(current_queue_item_id)
        if autoplay_enabled is not None:
            updates.append("autoplay_enabled = ?")
            params.append(1 if autoplay_enabled else 0)
        if shuffle_enabled is not None:
            updates.append("shuffle_enabled = ?")
            params.append(1 if shuffle_enabled else 0)
        if repeat_mode is not None:
            _require_choice(repeat_mode, PLAYBACK_REPEAT_MODES, "repeat_mode")
            updates.append("repeat_mode = ?")
            params.append(repeat_mode)
        if settings is not None:
            updates.append("settings_json = ?")
            params.append(_json_dumps(settings))
        if state is not None:
            updates.append("state_json = ?")
            params.append(_json_dumps(state))
        if not updates:
            return self.get_playback_session(session_id)
        updates.append("updated_at = ?")
        params.append(utc_now())
        params.extend((session_id, user_id))
        with self.connect() as conn:
            conn.execute(
                f"UPDATE playback_sessions SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
                params,
            )
            row = conn.execute(
                "SELECT * FROM playback_sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            ).fetchone()
        return row_to_playback_session(row) if row else None

    def list_queue_items(self, session_id: str, include_removed: bool = False) -> list[QueueItem]:
        if self.get_playback_session(session_id) is None:
            return []
        where = "session_id = ?"
        params: list[object] = [session_id]
        if not include_removed:
            where += " AND status != 'removed'"
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM queue_items
                WHERE {where}
                ORDER BY position, created_at, id
                """,
                params,
            ).fetchall()
        return [row_to_queue_item(row) for row in rows]

    def get_queue_item(self, queue_item_id: str) -> QueueItem | None:
        user_id = self.require_user_id()
        with self.connect() as conn:
            row = conn.execute(
                """SELECT q.* FROM queue_items q
                   JOIN playback_sessions s ON s.id = q.session_id
                   WHERE q.id = ? AND s.user_id = ?""",
                (queue_item_id, user_id),
            ).fetchone()
        return row_to_queue_item(row) if row else None

    def replace_queue_items(
        self,
        session_id: str,
        items: list[dict[str, object]],
    ) -> list[QueueItem]:
        if self.get_playback_session(session_id) is None:
            raise ValueError(f"Playback session not found: {session_id}")
        now = utc_now()
        with self.connect() as conn:
            if not _playback_session_exists(conn, session_id):
                raise ValueError(f"Playback session not found: {session_id}")
            _validate_track_ids(conn, [int(item["track_id"]) for item in items])
            conn.execute("DELETE FROM queue_items WHERE session_id = ?", (session_id,))
            inserted: list[str] = []
            for index, item in enumerate(items):
                queue_id = str(item.get("id") or uuid4())
                origin = str(item.get("origin") or "manual")
                _require_choice(origin, QUEUE_ORIGINS, "origin")
                status = str(item.get("status") or "queued")
                _require_choice(status, QUEUE_STATUSES, "status")
                source_type = item.get("source_type")
                if source_type is not None:
                    _require_choice(str(source_type), PLAYBACK_SOURCE_TYPES, "source_type")
                conn.execute(
                    """
                    INSERT INTO queue_items (
                        id, session_id, track_id, position, origin, source_type,
                        source_id, status, locked, reason, score, debug_json,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        queue_id,
                        session_id,
                        int(item["track_id"]),
                        int(item.get("position") or index),
                        origin,
                        str(source_type) if source_type is not None else None,
                        _optional_int(item.get("source_id")),
                        status,
                        1 if bool(item.get("locked", False)) else 0,
                        item.get("reason"),
                        _optional_float(item.get("score")),
                        _json_dumps(item.get("debug")) if isinstance(item.get("debug"), dict) else item.get("debug_json"),
                        now,
                        now,
                    ),
                )
                inserted.append(queue_id)
            current = inserted[0] if inserted else None
            current_track_id = int(items[0]["track_id"]) if inserted else None
            conn.execute(
                """
                UPDATE playback_sessions
                SET current_queue_item_id = ?, current_track_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (current, current_track_id, now, session_id),
            )
            rows = conn.execute(
                """
                SELECT * FROM queue_items
                WHERE session_id = ?
                ORDER BY position, created_at, id
                """,
                (session_id,),
            ).fetchall()
        return [row_to_queue_item(row) for row in rows]

    def append_queue_items(
        self,
        session_id: str,
        items: list[dict[str, object]],
    ) -> list[QueueItem]:
        if self.get_playback_session(session_id) is None:
            raise ValueError(f"Playback session not found: {session_id}")
        if not items:
            return self.list_queue_items(session_id)
        now = utc_now()
        with self.connect() as conn:
            if not _playback_session_exists(conn, session_id):
                raise ValueError(f"Playback session not found: {session_id}")
            _validate_track_ids(conn, [int(item["track_id"]) for item in items])
            max_position = conn.execute(
                "SELECT COALESCE(MAX(position), -1) FROM queue_items WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            for offset, item in enumerate(items, start=1):
                origin = str(item.get("origin") or "manual")
                _require_choice(origin, QUEUE_ORIGINS, "origin")
                source_type = item.get("source_type")
                if source_type is not None:
                    _require_choice(str(source_type), PLAYBACK_SOURCE_TYPES, "source_type")
                status = str(item.get("status") or "queued")
                _require_choice(status, QUEUE_STATUSES, "status")
                conn.execute(
                    """
                    INSERT INTO queue_items (
                        id, session_id, track_id, position, origin, source_type,
                        source_id, status, locked, reason, score, debug_json,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(item.get("id") or uuid4()),
                        session_id,
                        int(item["track_id"]),
                        int(max_position) + offset,
                        origin,
                        str(source_type) if source_type is not None else None,
                        _optional_int(item.get("source_id")),
                        status,
                        1 if bool(item.get("locked", False)) else 0,
                        item.get("reason"),
                        _optional_float(item.get("score")),
                        _json_dumps(item.get("debug")) if isinstance(item.get("debug"), dict) else item.get("debug_json"),
                        now,
                        now,
                    ),
                )
            conn.execute(
                _TOUCH_PLAYBACK_SESSION,
                (now, session_id),
            )
            rows = conn.execute(
                """
                SELECT * FROM queue_items
                WHERE session_id = ?
                ORDER BY position, created_at, id
                """,
                (session_id,),
            ).fetchall()
        return [row_to_queue_item(row) for row in rows]

    def remove_queue_item(self, session_id: str, queue_item_id: str) -> QueueItem | None:
        if self.get_playback_session(session_id) is None:
            return None
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE queue_items
                SET status = 'removed', updated_at = ?
                WHERE id = ? AND session_id = ?
                """,
                (now, queue_item_id, session_id),
            )
            conn.execute(
                _TOUCH_PLAYBACK_SESSION,
                (now, session_id),
            )
            row = conn.execute(
                "SELECT * FROM queue_items WHERE id = ? AND session_id = ?",
                (queue_item_id, session_id),
            ).fetchone()
        return row_to_queue_item(row) if row else None

    def move_queue_item(self, session_id: str, queue_item_id: str, position: int) -> list[QueueItem]:
        if self.get_playback_session(session_id) is None:
            raise ValueError(f"Playback session not found: {session_id}")
        if position < 0:
            raise ValueError("position must be non-negative")
        now = utc_now()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM queue_items
                WHERE session_id = ? AND status != 'removed'
                ORDER BY position, created_at, id
                """,
                (session_id,),
            ).fetchall()
            items = [row_to_queue_item(row) for row in rows]
            moving = next((item for item in items if item.id == queue_item_id), None)
            if moving is None:
                raise ValueError(f"Queue item not found: {queue_item_id}")
            remaining = [item for item in items if item.id != queue_item_id]
            insert_at = min(position, len(remaining))
            reordered = remaining[:insert_at] + [moving] + remaining[insert_at:]
            for index, item in enumerate(reordered):
                conn.execute(
                    "UPDATE queue_items SET position = ?, updated_at = ? WHERE id = ?",
                    (index + 100000, now, item.id),
                )
            for index, item in enumerate(reordered):
                conn.execute(
                    "UPDATE queue_items SET position = ?, updated_at = ? WHERE id = ?",
                    (index, now, item.id),
                )
            conn.execute(
                _TOUCH_PLAYBACK_SESSION,
                (now, session_id),
            )
            rows = conn.execute(
                """
                SELECT * FROM queue_items
                WHERE session_id = ? AND status != 'removed'
                ORDER BY position, created_at, id
                """,
                (session_id,),
            ).fetchall()
        return [row_to_queue_item(row) for row in rows]

    def jump_to_queue_item(self, session_id: str, queue_item_id: str) -> QueueItem | None:
        if self.get_playback_session(session_id) is None:
            return None
        now = utc_now()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM queue_items WHERE id = ? AND session_id = ?",
                (queue_item_id, session_id),
            ).fetchone()
            if row is None:
                return None
            item = row_to_queue_item(row)
            conn.execute(
                """
                UPDATE queue_items
                SET status = CASE WHEN id = ? THEN 'playing' ELSE status END,
                    updated_at = CASE WHEN id = ? THEN ? ELSE updated_at END
                WHERE session_id = ?
                """,
                (queue_item_id, queue_item_id, now, session_id),
            )
            conn.execute(
                """
                UPDATE playback_sessions
                SET current_queue_item_id = ?, current_track_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (queue_item_id, item.track_id, now, session_id),
            )
            refreshed = conn.execute(
                "SELECT * FROM queue_items WHERE id = ?",
                (queue_item_id,),
            ).fetchone()
        return row_to_queue_item(refreshed) if refreshed else None

    def record_playback_event(self, event: PlaybackEventCreate) -> PlaybackEventResult:
        user_id = self.require_user_id()
        _require_choice(event.event_type, PLAYBACK_EVENT_TYPES, "event_type")
        created_at = event.created_at or utc_now()
        event_id = event.event_id or str(uuid4())
        play_fraction = _normalized_play_fraction(
            event.play_fraction,
            position_seconds=event.position_seconds,
            duration_seconds=event.duration_seconds,
        )
        payload_json = _json_dumps(event.payload)
        session_id = event.session_id
        queue_item_id = event.queue_item_id
        track_id = event.track_id
        release_id = event.release_id
        artist_id = event.artist_id
        with self.connect() as conn:
            if event.client_event_id:
                duplicate = conn.execute(
                    "SELECT * FROM playback_events WHERE client_event_id = ? AND user_id = ?",
                    (event.client_event_id, user_id),
                ).fetchone()
                if duplicate is not None:
                    return PlaybackEventResult(
                        row_to_playback_event(duplicate),
                        True,
                        {"duplicate": True},
                    )
            if queue_item_id and track_id is None:
                queue_row = conn.execute(
                    "SELECT track_id, session_id FROM queue_items WHERE id = ?",
                    (queue_item_id,),
                ).fetchone()
                if queue_row is not None:
                    track_id = int(queue_row["track_id"])
                    session_id = session_id or str(queue_row["session_id"])
            if session_id is not None:
                owned = conn.execute(
                    "SELECT 1 FROM playback_sessions WHERE id = ? AND user_id = ?",
                    (session_id, user_id),
                ).fetchone()
                if owned is None:
                    raise ValueError(f"Playback session not found: {session_id}")
            if track_id is not None:
                release_id = release_id or _release_id_for_track(conn, track_id)
                artist_id = artist_id or _primary_artist_id_for_track(conn, track_id)
            conn.execute(
                """
                INSERT INTO playback_events (
                    id, user_id, session_id, queue_item_id, track_id, release_id, artist_id,
                    event_type, position_seconds, duration_seconds, play_fraction,
                    created_at, client_event_id, source, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    user_id,
                    session_id,
                    queue_item_id,
                    track_id,
                    release_id,
                    artist_id,
                    event.event_type,
                    event.position_seconds,
                    event.duration_seconds,
                    play_fraction,
                    created_at,
                    event.client_event_id,
                    event.source or "web",
                    payload_json,
                ),
            )
            event_row = conn.execute(
                "SELECT * FROM playback_events WHERE id = ? AND user_id = ?",
                (event_id, user_id),
            ).fetchone()
            delta = self._apply_playback_event_preferences(conn, event_row)
            self._apply_playback_event_session_state(conn, event_row)
        return PlaybackEventResult(row_to_playback_event(event_row), False, delta)

    def _apply_playback_event_session_state(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> None:
        event_type = str(row["event_type"])
        session_id = row["session_id"]
        queue_item_id = row["queue_item_id"]
        track_id = row["track_id"]
        now = str(row["created_at"])
        if session_id is None:
            return
        if event_type in {"track_started", "queue_click"} and queue_item_id:
            queue_row = conn.execute(
                "SELECT track_id FROM queue_items WHERE id = ? AND session_id = ?",
                (queue_item_id, session_id),
            ).fetchone()
            if queue_row is not None:
                conn.execute(
                    """
                    UPDATE queue_items
                    SET status = CASE WHEN id = ? THEN 'playing' ELSE status END,
                        updated_at = CASE WHEN id = ? THEN ? ELSE updated_at END
                    WHERE session_id = ?
                    """,
                    (queue_item_id, queue_item_id, now, session_id),
                )
                conn.execute(
                    """
                    UPDATE playback_sessions
                    SET current_queue_item_id = ?, current_track_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (queue_item_id, int(queue_row["track_id"]), now, session_id),
                )
            return
        if (
            event_type == "completed"
            and queue_item_id
            and playback_event_is_completion(row["position_seconds"], row["duration_seconds"], row["play_fraction"])
        ):
            conn.execute(
                "UPDATE queue_items SET status = 'played', updated_at = ? WHERE id = ?",
                (now, queue_item_id),
            )
        elif event_type == "skipped" and queue_item_id:
            conn.execute(
                "UPDATE queue_items SET status = 'skipped', updated_at = ? WHERE id = ?",
                (now, queue_item_id),
            )
        elif event_type == "removed_from_queue" and queue_item_id:
            conn.execute(
                "UPDATE queue_items SET status = 'removed', updated_at = ? WHERE id = ?",
                (now, queue_item_id),
            )
        if event_type == "autoplay_toggled":
            payload = _json_loads(row["payload_json"])
            enabled = payload.get("enabled")
            if isinstance(enabled, bool):
                conn.execute(
                    """
                    UPDATE playback_sessions
                    SET autoplay_enabled = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (1 if enabled else 0, now, session_id),
                )
        elif event_type in {"track_started", "queue_click"} and track_id is not None:
            conn.execute(
                """
                UPDATE playback_sessions
                SET current_track_id = ?, current_queue_item_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (int(track_id), queue_item_id, now, session_id),
            )

    def _apply_playback_event_preferences(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> dict[str, object]:
        event_type = str(row["event_type"])
        if event_type in {"track_started", "progress", "queue_click", "autoplay_toggled"}:
            return {}
        track_id = int(row["track_id"]) if row["track_id"] is not None else None
        release_id = int(row["release_id"]) if row["release_id"] is not None else None
        artist_ids = _artist_ids_for_event(conn, row)
        delta: dict[str, object] = {}
        if track_id is not None:
            before = self._get_track_preference_row(conn, track_id)
            self._apply_track_preference_event(conn, row, track_id)
            after = self._get_track_preference_row(conn, track_id)
            delta["track"] = _preference_delta_dict(before, after)
        if release_id is not None:
            self._apply_release_preference_event(conn, row, release_id, track_id is None)
            delta["release_id"] = release_id
        for artist_id in artist_ids:
            self._apply_artist_preference_event(conn, row, artist_id, track_id is None)
        if artist_ids:
            delta["artist_ids"] = artist_ids
        return delta

    def _get_track_preference_row(
        self,
        conn: sqlite3.Connection,
        track_id: int,
    ) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM user_track_preferences WHERE user_id = discocs_user_id() AND track_id = ?",
            (track_id,),
        ).fetchone()

    def _apply_track_preference_event(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        track_id: int,
    ) -> None:
        event_type = str(row["event_type"])
        created_at = str(row["created_at"])
        self._ensure_track_preference(conn, track_id, created_at)
        if event_type == "play_threshold_reached":
            conn.execute(
                """
                UPDATE user_track_preferences
                SET play_count = play_count + 1,
                    last_played_at = ?,
                    score = score + 0.25,
                    updated_at = ?
                WHERE user_id = discocs_user_id() AND track_id = ?
                """,
                (created_at, created_at, track_id),
            )
        elif event_type == "completed":
            if not playback_event_is_completion(
                row["position_seconds"],
                row["duration_seconds"],
                row["play_fraction"],
            ):
                return
            conn.execute(
                """
                UPDATE user_track_preferences
                SET completion_count = completion_count + 1,
                    last_completed_at = ?,
                    score = score + 1.0,
                    updated_at = ?
                WHERE user_id = discocs_user_id() AND track_id = ?
                """,
                (created_at, created_at, track_id),
            )
        elif event_type == "skipped":
            early = playback_event_is_early_skip(
                row["position_seconds"],
                row["duration_seconds"],
                row["play_fraction"],
            )
            score_delta = playback_skip_score_delta(
                row["position_seconds"],
                row["duration_seconds"],
                row["play_fraction"],
            )
            conn.execute(
                """
                UPDATE user_track_preferences
                SET skip_count = skip_count + 1,
                    early_skip_count = early_skip_count + ?,
                    last_skipped_at = ?,
                    score = score + ?,
                    updated_at = ?
                WHERE user_id = discocs_user_id() AND track_id = ?
                """,
                (1 if early else 0, created_at, score_delta, created_at, track_id),
            )
        elif event_type == "liked":
            conn.execute(
                """
                UPDATE user_track_preferences
                SET liked = 1, disliked = 0, score = score + CASE WHEN liked = 0 THEN 5.0 ELSE 0 END
                    + CASE WHEN disliked = 1 THEN 5.0 ELSE 0 END, updated_at = ?
                WHERE user_id = discocs_user_id() AND track_id = ?
                """,
                (created_at, track_id),
            )
        elif event_type == "unliked":
            conn.execute(
                """
                UPDATE user_track_preferences
                SET liked = 0, score = score - CASE WHEN liked = 1 THEN 5.0 ELSE 0 END,
                    updated_at = ?
                WHERE user_id = discocs_user_id() AND track_id = ?
                """,
                (created_at, track_id),
            )
        elif event_type == "disliked":
            conn.execute(
                """
                UPDATE user_track_preferences
                SET disliked = 1, liked = 0, score = score - CASE WHEN disliked = 0 THEN 5.0 ELSE 0 END
                    - CASE WHEN liked = 1 THEN 5.0 ELSE 0 END, updated_at = ?
                WHERE user_id = discocs_user_id() AND track_id = ?
                """,
                (created_at, track_id),
            )
        elif event_type == "replayed":
            conn.execute(
                """
                UPDATE user_track_preferences
                SET replay_count = replay_count + 1, score = score + 2.0, updated_at = ?
                WHERE user_id = discocs_user_id() AND track_id = ?
                """,
                (created_at, track_id),
            )
        elif event_type == "saved_to_playlist":
            conn.execute(
                """
                UPDATE user_track_preferences
                SET score = score + 3.0, updated_at = ?
                WHERE user_id = discocs_user_id() AND track_id = ?
                """,
                (created_at, track_id),
            )
        elif event_type == "removed_from_queue":
            conn.execute(
                """
                UPDATE user_track_preferences
                SET score = score - 1.0, updated_at = ?
                WHERE user_id = discocs_user_id() AND track_id = ?
                """,
                (created_at, track_id),
            )

    def _apply_release_preference_event(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        release_id: int,
        explicit_entity_event: bool,
    ) -> None:
        event_type = str(row["event_type"])
        created_at = str(row["created_at"])
        self._ensure_release_preference(conn, release_id, created_at)
        if event_type == "play_threshold_reached":
            conn.execute(
                """
                UPDATE user_release_preferences
                SET play_count = play_count + 1, last_played_at = ?,
                    score = score + 0.25, updated_at = ?
                WHERE user_id = discocs_user_id() AND release_id = ?
                """,
                (created_at, created_at, release_id),
            )
        elif event_type == "completed":
            if not playback_event_is_completion(
                row["position_seconds"],
                row["duration_seconds"],
                row["play_fraction"],
            ):
                return
            conn.execute(
                """
                UPDATE user_release_preferences
                SET completion_count = completion_count + 1, last_completed_at = ?,
                    score = score + 1.0, updated_at = ?
                WHERE user_id = discocs_user_id() AND release_id = ?
                """,
                (created_at, created_at, release_id),
            )
        elif event_type == "skipped":
            score_delta = playback_skip_score_delta(
                row["position_seconds"],
                row["duration_seconds"],
                row["play_fraction"],
            )
            conn.execute(
                """
                UPDATE user_release_preferences
                SET skip_count = skip_count + 1, score = score + ?, updated_at = ?
                WHERE user_id = discocs_user_id() AND release_id = ?
                """,
                (score_delta * 0.25, created_at, release_id),
            )
        elif event_type in {"liked", "saved_to_playlist"}:
            conn.execute(
                """
                UPDATE user_release_preferences
                SET liked = CASE WHEN ? THEN 1 ELSE liked END,
                    score = score + CASE WHEN ? THEN 5.0 ELSE 1.0 END,
                    updated_at = ?
                WHERE user_id = discocs_user_id() AND release_id = ?
                """,
                (1 if explicit_entity_event and event_type == "liked" else 0, 1 if event_type == "liked" else 0, created_at, release_id),
            )

    def _apply_artist_preference_event(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        artist_id: int,
        explicit_entity_event: bool,
    ) -> None:
        event_type = str(row["event_type"])
        created_at = str(row["created_at"])
        self._ensure_artist_preference(conn, artist_id, created_at)
        if event_type == "play_threshold_reached":
            conn.execute(
                """
                UPDATE user_artist_preferences
                SET play_count = play_count + 1, last_played_at = ?,
                    score = score + 0.25, updated_at = ?
                WHERE user_id = discocs_user_id() AND artist_id = ?
                """,
                (created_at, created_at, artist_id),
            )
        elif event_type == "completed":
            if not playback_event_is_completion(
                row["position_seconds"],
                row["duration_seconds"],
                row["play_fraction"],
            ):
                return
            conn.execute(
                """
                UPDATE user_artist_preferences
                SET completion_count = completion_count + 1,
                    score = score + 1.0, updated_at = ?
                WHERE user_id = discocs_user_id() AND artist_id = ?
                """,
                (created_at, artist_id),
            )
        elif event_type == "skipped":
            score_delta = playback_skip_score_delta(
                row["position_seconds"],
                row["duration_seconds"],
                row["play_fraction"],
            )
            conn.execute(
                """
                UPDATE user_artist_preferences
                SET skip_count = skip_count + 1, score = score + ?, updated_at = ?
                WHERE user_id = discocs_user_id() AND artist_id = ?
                """,
                (score_delta * 0.25, created_at, artist_id),
            )
        elif event_type in {"liked", "saved_to_playlist"}:
            conn.execute(
                """
                UPDATE user_artist_preferences
                SET liked = CASE WHEN ? THEN 1 ELSE liked END,
                    score = score + CASE WHEN ? THEN 5.0 ELSE 1.0 END,
                    updated_at = ?
                WHERE user_id = discocs_user_id() AND artist_id = ?
                """,
                (1 if explicit_entity_event and event_type == "liked" else 0, 1 if event_type == "liked" else 0, created_at, artist_id),
            )

    def _ensure_track_preference(
        self,
        conn: sqlite3.Connection,
        track_id: int,
        updated_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT OR IGNORE INTO user_track_preferences (user_id, track_id, updated_at)
            VALUES (discocs_user_id(), ?, ?)
            """,
            (track_id, updated_at),
        )

    def _ensure_release_preference(
        self,
        conn: sqlite3.Connection,
        release_id: int,
        updated_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT OR IGNORE INTO user_release_preferences (user_id, release_id, updated_at)
            VALUES (discocs_user_id(), ?, ?)
            """,
            (release_id, updated_at),
        )

    def _ensure_artist_preference(
        self,
        conn: sqlite3.Connection,
        artist_id: int,
        updated_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT OR IGNORE INTO user_artist_preferences (user_id, artist_id, updated_at)
            VALUES (discocs_user_id(), ?, ?)
            """,
            (artist_id, updated_at),
        )

    def get_track_preference(self, track_id: int) -> UserTrackPreference | None:
        self.require_user_id()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_track_preferences WHERE user_id = discocs_user_id() AND track_id = ?",
                (track_id,),
            ).fetchone()
        return row_to_user_track_preference(row) if row else None

    def recently_played_track_ids(self, within_days: int) -> set[int]:
        """Track IDs played within the last ``within_days`` days.

        Compares ``user_track_preferences.last_played_at`` against a cutoff
        timestamp. Timestamps come from two sources with slightly different
        suffixes (local playback uses ``+00:00``, Navidrome uses ``Z``), but the
        ``YYYY-MM-DDTHH:MM:SS`` prefix is lexically comparable for both, so a
        suffix-free cutoff string is a correct second-resolution bound.
        """
        if within_days <= 0:
            return set()
        cutoff = (datetime.now(UTC) - timedelta(days=within_days)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT track_id
                FROM user_track_preferences
                WHERE user_id = discocs_user_id()
                  AND last_played_at IS NOT NULL AND last_played_at >= ?
                """,
                (cutoff,),
            ).fetchall()
        return {int(row["track_id"]) for row in rows}

    def import_external_track_play_state(
        self,
        track_id: int,
        *,
        play_count: int | None = None,
        last_played_at: str | None = None,
        liked: bool | None = None,
    ) -> UserTrackPreference | None:
        with self.connect() as conn:
            self._import_external_track_play_state(
                conn,
                track_id,
                play_count=play_count,
                last_played_at=last_played_at,
                liked=liked,
            )
            row = self._get_track_preference_row(conn, track_id)
        return row_to_user_track_preference(row) if row else None

    def _import_external_track_play_state(
        self,
        conn: sqlite3.Connection,
        track_id: int,
        *,
        play_count: int | None = None,
        last_played_at: str | None = None,
        liked: bool | None = None,
    ) -> None:
        if play_count is None and last_played_at is None and liked is None:
            return
        now = utc_now()
        self._ensure_track_preference(conn, track_id, now)
        bounded_play_count = max(int(play_count), 0) if play_count is not None else None
        play_score = min(float(bounded_play_count or 0) * 0.25, 5.0)
        conn.execute(
            """
            UPDATE user_track_preferences
            SET play_count = CASE
                    WHEN ? IS NULL THEN play_count
                    WHEN play_count < ? THEN ?
                    ELSE play_count
                END,
                last_played_at = CASE
                    WHEN ? IS NULL THEN last_played_at
                    WHEN last_played_at IS NULL OR ? > last_played_at THEN ?
                    ELSE last_played_at
                END,
                liked = CASE WHEN ? IS NULL THEN liked WHEN ? THEN 1 ELSE liked END,
                disliked = CASE WHEN ? THEN 0 ELSE disliked END,
                score = MAX(score, ? + CASE WHEN ? THEN 5.0 ELSE 0 END),
                updated_at = ?
            WHERE user_id = discocs_user_id() AND track_id = ?
            """,
            (
                bounded_play_count,
                bounded_play_count,
                bounded_play_count,
                last_played_at,
                last_played_at,
                last_played_at,
                liked,
                1 if liked else 0,
                1 if liked else 0,
                play_score,
                1 if liked else 0,
                now,
                track_id,
            ),
        )
        release_id = _release_id_for_track(conn, track_id)
        if release_id is not None:
            self._ensure_release_preference(conn, release_id, now)
            conn.execute(
                """
                UPDATE user_release_preferences
                SET play_count = CASE
                        WHEN ? IS NULL THEN play_count
                        WHEN play_count < ? THEN ?
                        ELSE play_count
                    END,
                    last_played_at = CASE
                        WHEN ? IS NULL THEN last_played_at
                        WHEN last_played_at IS NULL OR ? > last_played_at THEN ?
                        ELSE last_played_at
                    END,
                    liked = CASE WHEN ? THEN 1 ELSE liked END,
                    score = MAX(score, ? + CASE WHEN ? THEN 5.0 ELSE 0 END),
                    updated_at = ?
                WHERE user_id = discocs_user_id() AND release_id = ?
                """,
                (
                    bounded_play_count,
                    bounded_play_count,
                    bounded_play_count,
                    last_played_at,
                    last_played_at,
                    last_played_at,
                    1 if liked else 0,
                    play_score,
                    1 if liked else 0,
                    now,
                    release_id,
                ),
            )
        for artist_id in _artist_ids_for_track(conn, track_id):
            self._ensure_artist_preference(conn, artist_id, now)
            conn.execute(
                """
                UPDATE user_artist_preferences
                SET play_count = CASE
                        WHEN ? IS NULL THEN play_count
                        WHEN play_count < ? THEN ?
                        ELSE play_count
                    END,
                    last_played_at = CASE
                        WHEN ? IS NULL THEN last_played_at
                        WHEN last_played_at IS NULL OR ? > last_played_at THEN ?
                        ELSE last_played_at
                    END,
                    liked = CASE WHEN ? THEN 1 ELSE liked END,
                    score = MAX(score, ? + CASE WHEN ? THEN 5.0 ELSE 0 END),
                    updated_at = ?
                WHERE user_id = discocs_user_id() AND artist_id = ?
                """,
                (
                    bounded_play_count,
                    bounded_play_count,
                    bounded_play_count,
                    last_played_at,
                    last_played_at,
                    last_played_at,
                    1 if liked else 0,
                    play_score,
                    1 if liked else 0,
                    now,
                    artist_id,
                ),
            )

    def get_release_preference(self, release_id: int) -> UserReleasePreference | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_release_preferences WHERE user_id = discocs_user_id() AND release_id = ?",
                (release_id,),
            ).fetchone()
        return row_to_user_release_preference(row) if row else None

    def get_artist_preference(self, artist_id: int) -> UserArtistPreference | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_artist_preferences WHERE user_id = discocs_user_id() AND artist_id = ?",
                (artist_id,),
            ).fetchone()
        return row_to_user_artist_preference(row) if row else None

    def set_artist_liked(self, artist_id: int, liked: bool) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO user_artist_preferences (user_id, artist_id, updated_at) VALUES (discocs_user_id(), ?, ?)",
                (artist_id, now),
            )
            conn.execute(
                "UPDATE user_artist_preferences SET liked = ?, updated_at = ? WHERE user_id = discocs_user_id() AND artist_id = ?",
                (1 if liked else 0, now, artist_id),
            )

    def sync_artist_liked_from_navidrome(self, artist_ids: list[int]) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute("UPDATE user_artist_preferences SET liked = 0 WHERE user_id = discocs_user_id() AND liked = 1")
            for artist_id in artist_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO user_artist_preferences (user_id, artist_id, updated_at) VALUES (discocs_user_id(), ?, ?)",
                    (artist_id, now),
                )
                conn.execute(
                    "UPDATE user_artist_preferences SET liked = 1, updated_at = ? WHERE user_id = discocs_user_id() AND artist_id = ?",
                    (now, artist_id),
                )

    def list_playback_events(self, session_id: str | None = None) -> list[PlaybackEvent]:
        self.require_user_id()
        where = "WHERE user_id = discocs_user_id()"
        params: list[object] = []
        if session_id is not None:
            where += " AND session_id = ?"
            params.append(session_id)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM playback_events
                {where}
                ORDER BY created_at, id
                """,
                params,
            ).fetchall()
        return [row_to_playback_event(row) for row in rows]

