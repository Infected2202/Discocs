"""Store Row converters and utility functions.

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


logger = logging.getLogger(__name__)
INIT_LOCK = Lock()
INITIALIZED_DB_PATHS: set[Path] = set()

def row_to_track(row: sqlite3.Row) -> Track:
    return Track(
        id=int(row["id"]),
        path=str(row["path"]),
        artist=row["artist"],
        title=row["title"],
        album=row["album"],
        genre=row["genre"],
        year=int(row["year"]) if row["year"] is not None else None,
        duration=float(row["duration"]) if row["duration"] is not None else None,
        file_size=int(row["file_size"]),
        mtime=int(row["mtime"]),
        missing_at=row["missing_at"] if "missing_at" in row.keys() else None,
        added_at=row["added_at"] if "added_at" in row.keys() else None,
    )


def row_to_external_track(row: sqlite3.Row) -> ExternalTrack:
    return ExternalTrack(
        provider=str(row["provider"]),
        external_id=str(row["external_id"]),
        track_id=int(row["track_id"]),
        raw_json=row["raw_json"],
        synced_at=str(row["synced_at"]),
    )


def row_to_artist(row: sqlite3.Row) -> Artist:
    return Artist(
        id=int(row["id"]),
        name=str(row["name"]),
        sort_name=row["sort_name"],
        normalized_name=str(row["normalized_name"]),
        image_url=row["image_url"] if "image_url" in row.keys() else None,
        bio=row["bio"] if "bio" in row.keys() else None,
    )


def row_to_release(row: sqlite3.Row) -> Release:
    return Release(
        id=int(row["id"]),
        title=str(row["title"]),
        normalized_title=str(row["normalized_title"]),
        release_type=str(row["release_type"]),
        release_date=row["release_date"],
        release_year=int(row["release_year"]) if row["release_year"] is not None else None,
        cover_art_id=row["cover_art_id"],
        track_count=int(row["track_count"] or 0),
        duration=float(row["duration"]) if row["duration"] is not None else None,
        label=row["label"],
        catalog_number=row["catalog_number"],
        identity_key=str(row["identity_key"]),
        identity_confidence=str(row["identity_confidence"]),
        added_at=row["added_at"] if "added_at" in row.keys() else None,
    )


def row_to_playback_session(row: sqlite3.Row) -> PlaybackSession:
    return PlaybackSession(
        id=str(row["id"]),
        source_type=str(row["source_type"]),
        source_id=int(row["source_id"]) if row["source_id"] is not None else None,
        source_label=row["source_label"],
        mode=str(row["mode"]),
        status=str(row["status"]),
        current_track_id=int(row["current_track_id"]) if row["current_track_id"] is not None else None,
        current_queue_item_id=row["current_queue_item_id"],
        autoplay_enabled=bool(row["autoplay_enabled"]),
        shuffle_enabled=bool(row["shuffle_enabled"]),
        repeat_mode=str(row["repeat_mode"]),
        started_at=str(row["started_at"]),
        updated_at=str(row["updated_at"]),
        ended_at=row["ended_at"],
        settings_json=row["settings_json"],
        state_json=row["state_json"],
    )


def row_to_queue_item(row: sqlite3.Row) -> QueueItem:
    return QueueItem(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        track_id=int(row["track_id"]),
        position=int(row["position"]),
        origin=str(row["origin"]),
        source_type=row["source_type"],
        source_id=int(row["source_id"]) if row["source_id"] is not None else None,
        status=str(row["status"]),
        locked=bool(row["locked"]),
        reason=row["reason"],
        score=float(row["score"]) if row["score"] is not None else None,
        debug_json=row["debug_json"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def row_to_playback_event(row: sqlite3.Row) -> PlaybackEvent:
    return PlaybackEvent(
        id=str(row["id"]),
        session_id=row["session_id"],
        queue_item_id=row["queue_item_id"],
        track_id=int(row["track_id"]) if row["track_id"] is not None else None,
        release_id=int(row["release_id"]) if row["release_id"] is not None else None,
        artist_id=int(row["artist_id"]) if row["artist_id"] is not None else None,
        event_type=str(row["event_type"]),
        position_seconds=float(row["position_seconds"]) if row["position_seconds"] is not None else None,
        duration_seconds=float(row["duration_seconds"]) if row["duration_seconds"] is not None else None,
        play_fraction=float(row["play_fraction"]) if row["play_fraction"] is not None else None,
        created_at=str(row["created_at"]),
        client_event_id=row["client_event_id"],
        source=str(row["source"]),
        payload_json=row["payload_json"],
    )


def row_to_user_track_preference(row: sqlite3.Row) -> UserTrackPreference:
    return UserTrackPreference(
        track_id=int(row["track_id"]),
        liked=bool(row["liked"]),
        disliked=bool(row["disliked"]),
        play_count=int(row["play_count"]),
        completion_count=int(row["completion_count"]),
        skip_count=int(row["skip_count"]),
        early_skip_count=int(row["early_skip_count"]),
        replay_count=int(row["replay_count"]),
        last_played_at=row["last_played_at"],
        last_completed_at=row["last_completed_at"],
        last_skipped_at=row["last_skipped_at"],
        score=float(row["score"]),
        updated_at=str(row["updated_at"]),
    )


def row_to_user_release_preference(row: sqlite3.Row) -> UserReleasePreference:
    return UserReleasePreference(
        release_id=int(row["release_id"]),
        liked=bool(row["liked"]),
        play_count=int(row["play_count"]),
        completion_count=int(row["completion_count"]),
        skip_count=int(row["skip_count"]),
        last_played_at=row["last_played_at"],
        last_completed_at=row["last_completed_at"],
        score=float(row["score"]),
        updated_at=str(row["updated_at"]),
    )


def row_to_user_artist_preference(row: sqlite3.Row) -> UserArtistPreference:
    return UserArtistPreference(
        artist_id=int(row["artist_id"]),
        liked=bool(row["liked"]),
        play_count=int(row["play_count"]),
        completion_count=int(row["completion_count"]),
        skip_count=int(row["skip_count"]),
        last_played_at=row["last_played_at"],
        score=float(row["score"]),
        updated_at=str(row["updated_at"]),
    )


def row_to_generated_mix(row: sqlite3.Row) -> GeneratedMix:
    return GeneratedMix(
        id=str(row["id"]),
        title=str(row["title"]),
        mix_type=str(row["mix_type"]),
        status=str(row["status"]),
        cover_path=row["cover_path"],
        anchor_json=row["anchor_json"],
        settings_json=row["settings_json"],
        score_summary_json=row["score_summary_json"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        expires_at=row["expires_at"],
        saved_playlist_id=int(row["saved_playlist_id"]) if row["saved_playlist_id"] is not None else None,
    )


def row_to_generated_mix_item(row: sqlite3.Row) -> GeneratedMixItem:
    return GeneratedMixItem(
        mix_id=str(row["mix_id"]),
        position=int(row["position"]),
        track_id=int(row["track_id"]),
        score=float(row["score"]) if row["score"] is not None else None,
        score_breakdown_json=row["score_breakdown_json"],
        reason_json=row["reason_json"],
        created_at=str(row["created_at"]),
    )


def row_to_playlist(row: sqlite3.Row) -> Playlist:
    return Playlist(
        id=int(row["id"]),
        title=str(row["title"]),
        kind=str(row["kind"]),
        description=row["description"],
        cover_path=row["cover_path"],
        source_json=row["source_json"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def row_to_playlist_item(row: sqlite3.Row) -> PlaylistItem:
    return PlaylistItem(
        playlist_id=int(row["playlist_id"]),
        position=int(row["position"]),
        track_id=int(row["track_id"]),
        created_at=str(row["created_at"]),
    )


def _discography_group_key(release_type: str, featured_only: bool) -> str:
    if featured_only:
        return "featured_in"
    return {
        "album": "albums",
        "ep": "eps",
        "single": "singles",
        "compilation": "compilations",
    }.get(release_type, "releases")


def _envelope_from_track_with_external(
    row: sqlite3.Row,
    fallback: TrackMetadataEnvelope,
) -> TrackMetadataEnvelope:
    raw_json = row["external_raw_json"]
    raw: dict[str, object] = {}
    if raw_json:
        try:
            decoded = json.loads(str(raw_json))
            raw = decoded if isinstance(decoded, dict) else {}
        except json.JSONDecodeError:
            raw = {}
    album_id = _raw_value(raw, "albumId", "album_id")
    album_artist = _raw_value(raw, "albumArtist", "albumartist", "album_artist")
    cover_art_id = _raw_value(raw, "coverArt", "coverArtId", "cover_art_id")
    release_date = _raw_value(raw, "releaseDate", "date")
    release_type = _raw_release_type(_raw_value(raw, "releaseType", "albumType", "mediaType"))
    return TrackMetadataEnvelope(
        title=fallback.title,
        artist=fallback.artist,
        album=fallback.album,
        album_artist=album_artist or fallback.album_artist,
        genre=fallback.genre,
        year=fallback.year,
        duration=fallback.duration,
        path=fallback.path,
        track_number=_raw_int(raw, "track", "trackNumber", "track_number"),
        disc_number=_raw_int(raw, "discNumber", "disc_number"),
        total_tracks=_raw_int(raw, "totalTracks", "trackTotal", "total_tracks"),
        release_type=release_type,
        release_date=release_date,
        cover_art_id=cover_art_id,
        provider=str(row["external_provider"]),
        provider_track_id=str(row["external_id"]),
        provider_release_id=album_id,
        provider_artist_id=_raw_value(raw, "artistId", "artist_id"),
        raw_json=raw_json,
    )


def _raw_value(raw: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, dict):
            value = value.get("id") or value.get("name") or value.get("title")
        if isinstance(value, list):
            value = value[0] if value else None
        text = clean_display_text(str(value)) if value is not None else None
        if text:
            return text
    return None


def _raw_int(raw: dict[str, object], *keys: str) -> int | None:
    value = _raw_value(raw, *keys)
    if not value:
        return None
    first = value.split("/", 1)[0].strip()
    return int(first) if first.isdigit() else None


def _raw_release_type(value: str | None) -> str:
    normalized = normalize_text(value)
    return {
        "album": "album",
        "ep": "ep",
        "single": "single",
        "compilation": "compilation",
        "soundtrack": "soundtrack",
        "mix": "mix",
    }.get(normalized, "unknown")


def _require_choice(value: str, allowed: set[str], name: str) -> str:
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"{name} must be one of: {choices}")
    return value


def _json_dumps(value: object | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _json_loads(value: str | None) -> dict[str, object]:
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _optional_int(value: object | None) -> int | None:
    return int(value) if value is not None else None


def _optional_float(value: object | None) -> float | None:
    return float(value) if value is not None else None


def _normalized_play_fraction(
    play_fraction: float | None,
    *,
    position_seconds: float | None,
    duration_seconds: float | None,
) -> float | None:
    if play_fraction is not None:
        return max(0.0, min(1.0, float(play_fraction)))
    if position_seconds is None or duration_seconds is None or duration_seconds <= 0:
        return None
    return max(0.0, min(1.0, float(position_seconds) / float(duration_seconds)))


def playback_event_is_early_skip(
    position_seconds: object | None,
    duration_seconds: object | None,
    play_fraction: object | None,
) -> bool:
    if position_seconds is not None and float(position_seconds) < EARLY_SKIP_SECONDS:
        return True
    fraction = _normalized_play_fraction(
        float(play_fraction) if play_fraction is not None else None,
        position_seconds=float(position_seconds) if position_seconds is not None else None,
        duration_seconds=float(duration_seconds) if duration_seconds is not None else None,
    )
    return fraction is not None and fraction < EARLY_SKIP_FRACTION


def playback_event_is_completion(
    position_seconds: object | None,
    duration_seconds: object | None,
    play_fraction: object | None,
) -> bool:
    fraction = _normalized_play_fraction(
        float(play_fraction) if play_fraction is not None else None,
        position_seconds=float(position_seconds) if position_seconds is not None else None,
        duration_seconds=float(duration_seconds) if duration_seconds is not None else None,
    )
    return fraction is None or fraction >= COMPLETION_FRACTION


def playback_skip_score_delta(
    position_seconds: object | None,
    duration_seconds: object | None,
    play_fraction: object | None,
) -> float:
    if playback_event_is_early_skip(position_seconds, duration_seconds, play_fraction):
        return -2.0
    fraction = _normalized_play_fraction(
        float(play_fraction) if play_fraction is not None else None,
        position_seconds=float(position_seconds) if position_seconds is not None else None,
        duration_seconds=float(duration_seconds) if duration_seconds is not None else None,
    )
    if fraction is not None and fraction >= LATE_SKIP_FRACTION:
        return -0.1
    return -1.0


def _playback_session_exists(conn: sqlite3.Connection, session_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM playback_sessions WHERE id = ?",
        (session_id,),
    ).fetchone() is not None


def _validate_track_ids(conn: sqlite3.Connection, track_ids: list[int]) -> None:
    if not track_ids:
        return
    unique_ids = sorted(set(int(track_id) for track_id in track_ids))
    placeholders = ", ".join("?" for _ in unique_ids)
    rows = conn.execute(
        f"SELECT id FROM tracks WHERE id IN ({placeholders})",
        unique_ids,
    ).fetchall()
    found = {int(row["id"]) for row in rows}
    missing = [track_id for track_id in unique_ids if track_id not in found]
    if missing:
        formatted = ", ".join(str(track_id) for track_id in missing)
        raise ValueError(f"Tracks not found: {formatted}")


def _release_id_for_track(conn: sqlite3.Connection, track_id: int) -> int | None:
    row = conn.execute(
        """
        SELECT release_id
        FROM release_tracks
        WHERE track_id = ?
        ORDER BY position
        LIMIT 1
        """,
        (track_id,),
    ).fetchone()
    return int(row["release_id"]) if row else None


def _primary_artist_id_for_track(conn: sqlite3.Connection, track_id: int) -> int | None:
    row = conn.execute(
        """
        SELECT artist_id
        FROM track_artists
        WHERE track_id = ? AND role = 'primary'
        ORDER BY position
        LIMIT 1
        """,
        (track_id,),
    ).fetchone()
    return int(row["artist_id"]) if row else None


def _artist_ids_for_track(conn: sqlite3.Connection, track_id: int) -> list[int]:
    rows = conn.execute(
        """
        SELECT artist_id
        FROM track_artists
        WHERE track_id = ? AND role = 'primary'
        ORDER BY position
        """,
        (track_id,),
    ).fetchall()
    return [int(item["artist_id"]) for item in rows]


def _artist_ids_for_event(conn: sqlite3.Connection, row: sqlite3.Row) -> list[int]:
    if row["artist_id"] is not None:
        return [int(row["artist_id"])]
    if row["track_id"] is None:
        return []
    return _artist_ids_for_track(conn, int(row["track_id"]))


def _preference_delta_dict(
    before: sqlite3.Row | None,
    after: sqlite3.Row | None,
) -> dict[str, object]:
    if after is None:
        return {}
    fields = [
        "liked",
        "disliked",
        "play_count",
        "completion_count",
        "skip_count",
        "early_skip_count",
        "replay_count",
        "score",
    ]
    delta: dict[str, object] = {"track_id": int(after["track_id"])}
    for field in fields:
        old = before[field] if before is not None else 0
        new = after[field]
        if old != new:
            delta[field] = new
    return delta


def row_to_instant_mix_request(row: sqlite3.Row) -> InstantMixRequest:
    return InstantMixRequest(
        id=str(row["id"]),
        provider=str(row["provider"]),
        seed_item_id=str(row["seed_item_id"]),
        seed_track_id=int(row["seed_track_id"]) if row["seed_track_id"] is not None else None,
        model_name=str(row["model_name"]),
        requested_count=int(row["requested_count"]) if row["requested_count"] is not None else None,
        effective_count=int(row["effective_count"]),
        max_per_artist=int(row["max_per_artist"]),
        exclude_same_album=bool(row["exclude_same_album"]),
        min_similarity=float(row["min_similarity"]) if row["min_similarity"] is not None else None,
        status=str(row["status"]),
        result_count=int(row["result_count"]),
        skipped_without_external_id=int(row["skipped_without_external_id"]),
        duration_ms=float(row["duration_ms"]) if row["duration_ms"] is not None else None,
        error=row["error"],
        params_json=str(row["params_json"]),
        results_json=str(row["results_json"]),
        created_at=str(row["created_at"]),
    )


def _require_external_value(value: str, name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    return cleaned


def row_to_analysis_task(row: sqlite3.Row | dict[str, object]) -> AnalysisTask:
    return AnalysisTask(
        id=str(row["id"]),
        job_id=str(row["job_id"]),
        track_id=int(row["track_id"]),
        model_name=str(row["model_name"]),
        status=str(row["status"]),
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        path=str(row["path"]),
        file_size=int(row["file_size"]),
        mtime=int(row["mtime"]),
        error=row["error"],
        error_type=row["error_type"],
        stage=row["stage"],
    )


def row_to_analysis_job(row: sqlite3.Row) -> AnalysisJob:
    return AnalysisJob(
        id=str(row["id"]),
        kind=str(row["kind"] or "analyze"),
        model_name=str(row["model_name"]),
        status=str(row["status"]),
        total=int(row["total"] or 0),
        done=int(row["done"] or 0),
        failed=int(row["failed"] or 0),
        queued=int(row["queued"] or 0),
        leased=int(row["leased"] or 0),
        final_failed=int(row["final_failed"] or 0),
        message=str(row["message"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        finished_at=row["finished_at"],
    )


def row_to_analysis_worker(row: sqlite3.Row) -> AnalysisWorker:
    return AnalysisWorker(
        worker_id=str(row["worker_id"]),
        models=str(row["models"]),
        status=str(row["status"]),
        last_seen_at=str(row["last_seen_at"]),
        created_at=str(row["created_at"]),
        claimed_count=int(row["claimed_count"] or 0),
        completed_count=int(row["completed_count"] or 0),
        failed_count=int(row["failed_count"] or 0),
        released_count=int(row["released_count"] or 0),
        current_task_id=row["current_task_id"],
        stage=row["stage"],
        message=row["message"],
    )


def track_dict(track: Track) -> dict[str, object]:
    return {
        "id": track.id,
        "path": track.path,
        "artist": track.artist,
        "title": track.title,
        "album": track.album,
        "genre": track.genre,
        "year": track.year,
        "duration": track.duration,
        "file_size": track.file_size,
        "missing_at": track.missing_at,
    }


def track_listing_dict(listing: TrackListing) -> dict[str, object]:
    data = track_dict(listing.track)
    data["has_embedding"] = listing.has_embedding
    data["predicted_genres"] = [
        {
            "label": prediction.label,
            "score": prediction.score,
            "rank": prediction.rank,
        }
        for prediction in (listing.predictions or [])
    ]
    return data


def similar_track_dict(result: SimilarTrack) -> dict[str, object]:
    data = track_dict(result.track)
    data["distance"] = result.distance
    data["similarity"] = result.similarity
    data["rating"] = result.rating
    return data
