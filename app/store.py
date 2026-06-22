from __future__ import annotations

import logging
import json
import sqlite3
from dataclasses import dataclass
from dataclasses import replace
from datetime import UTC, datetime, timedelta
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
from app.scanner import ScannedTrack


logger = logging.getLogger(__name__)
INIT_LOCK = Lock()
INITIALIZED_DB_PATHS: set[Path] = set()


@dataclass(frozen=True)
class Track:
    id: int
    path: str
    artist: str | None
    title: str | None
    album: str | None
    duration: float | None
    file_size: int
    mtime: int
    genre: str | None = None
    year: int | None = None
    missing_at: str | None = None
    added_at: str | None = None


@dataclass(frozen=True)
class ExternalTrack:
    provider: str
    external_id: str
    track_id: int
    raw_json: str | None
    synced_at: str


@dataclass(frozen=True)
class Artist:
    id: int
    name: str
    sort_name: str | None
    normalized_name: str
    image_url: str | None = None
    bio: str | None = None


@dataclass(frozen=True)
class Release:
    id: int
    title: str
    normalized_title: str
    release_type: str
    release_date: str | None
    release_year: int | None
    cover_art_id: str | None
    track_count: int
    duration: float | None
    label: str | None
    catalog_number: str | None
    identity_key: str
    identity_confidence: str
    added_at: str | None = None


@dataclass(frozen=True)
class ArtistSummaryRow:
    artist: Artist
    track_count: int
    release_count: int


@dataclass(frozen=True)
class ReleaseSummaryRow:
    release: Release
    artists: list[Artist]


@dataclass(frozen=True)
class ReleaseTrackRow:
    track: Track
    disc_number: int | None
    track_number: int | None
    position: int
    artists: list[Artist]


@dataclass(frozen=True)
class NormalizationStatus:
    total_tracks: int
    tracks_with_release: int
    tracks_with_artist: int
    releases: int
    artists: int
    orphan_releases: int
    orphan_artists: int


@dataclass(frozen=True)
class InstantMixRequest:
    id: str
    provider: str
    seed_item_id: str
    seed_track_id: int | None
    model_name: str
    requested_count: int | None
    effective_count: int
    max_per_artist: int
    exclude_same_album: bool
    min_similarity: float | None
    status: str
    result_count: int
    skipped_without_external_id: int
    duration_ms: float | None
    error: str | None
    params_json: str
    results_json: str
    created_at: str


@dataclass(frozen=True)
class TrackListing:
    track: Track
    has_embedding: bool
    predictions: list["TrackPrediction"] | None = None


@dataclass(frozen=True)
class SimilarTrack:
    track: Track
    distance: float
    similarity: float
    rating: int | None = None


@dataclass(frozen=True)
class TrackPrediction:
    label: str
    score: float
    rank: int


@dataclass(frozen=True)
class TrackModelOutput:
    model_name: str
    scores: np.ndarray
    aggregation: str
    dtype: str = "float32"


@dataclass(frozen=True)
class TrackFeature:
    name: str
    value: float | None = None
    text_value: str | None = None
    unit: str | None = None
    confidence: float | None = None
    extractor: str = ""


@dataclass(frozen=True)
class FeatureSummary:
    name: str
    extractor: str
    value_count: int
    text_count: int
    track_count: int
    min_value: float | None = None
    max_value: float | None = None
    avg_value: float | None = None
    unit: str | None = None


@dataclass(frozen=True)
class FeatureFilter:
    name: str
    min_value: float | None = None
    max_value: float | None = None
    text_values: tuple[str, ...] = ()


@dataclass(frozen=True)
class FeatureTrack:
    track: Track
    features: list[TrackFeature]


@dataclass(frozen=True)
class HeadSummary:
    model_name: str
    output_count: int
    prediction_track_count: int
    label_count: int
    max_score: float | None = None
    avg_score: float | None = None


@dataclass(frozen=True)
class AnalysisJob:
    id: str
    kind: str
    model_name: str
    status: str
    total: int
    done: int
    failed: int
    queued: int
    leased: int
    final_failed: int
    message: str
    created_at: str
    updated_at: str
    finished_at: str | None = None


@dataclass(frozen=True)
class AnalysisTask:
    id: str
    job_id: str
    track_id: int
    model_name: str
    status: str
    attempts: int
    max_attempts: int
    lease_owner: str | None
    lease_expires_at: str | None
    path: str
    file_size: int
    mtime: int
    error: str | None = None
    error_type: str | None = None
    stage: str | None = None


@dataclass(frozen=True)
class AnalysisWorker:
    worker_id: str
    models: str
    status: str
    last_seen_at: str
    created_at: str
    claimed_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    released_count: int = 0
    current_task_id: str | None = None
    stage: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class PlaybackSession:
    id: str
    source_type: str
    source_id: int | None
    source_label: str | None
    mode: str
    status: str
    current_track_id: int | None
    current_queue_item_id: str | None
    autoplay_enabled: bool
    shuffle_enabled: bool
    repeat_mode: str
    started_at: str
    updated_at: str
    ended_at: str | None
    settings_json: str | None
    state_json: str | None


@dataclass(frozen=True)
class QueueItem:
    id: str
    session_id: str
    track_id: int
    position: int
    origin: str
    source_type: str | None
    source_id: int | None
    status: str
    locked: bool
    reason: str | None
    score: float | None
    debug_json: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PlaybackEvent:
    id: str
    session_id: str | None
    queue_item_id: str | None
    track_id: int | None
    release_id: int | None
    artist_id: int | None
    event_type: str
    position_seconds: float | None
    duration_seconds: float | None
    play_fraction: float | None
    created_at: str
    client_event_id: str | None
    source: str
    payload_json: str | None


@dataclass(frozen=True)
class UserTrackPreference:
    track_id: int
    liked: bool
    disliked: bool
    play_count: int
    completion_count: int
    skip_count: int
    early_skip_count: int
    replay_count: int
    last_played_at: str | None
    last_completed_at: str | None
    last_skipped_at: str | None
    score: float
    updated_at: str


@dataclass(frozen=True)
class UserReleasePreference:
    release_id: int
    liked: bool
    play_count: int
    completion_count: int
    skip_count: int
    last_played_at: str | None
    last_completed_at: str | None
    score: float
    updated_at: str


@dataclass(frozen=True)
class UserArtistPreference:
    artist_id: int
    liked: bool
    play_count: int
    completion_count: int
    skip_count: int
    last_played_at: str | None
    score: float
    updated_at: str


@dataclass(frozen=True)
class PlaybackEventResult:
    event: PlaybackEvent
    duplicate: bool
    preference_delta: dict[str, object]


@dataclass(frozen=True)
class GeneratedMix:
    id: str
    title: str
    mix_type: str
    status: str
    anchor_json: str | None
    settings_json: str | None
    score_summary_json: str | None
    created_at: str
    updated_at: str
    expires_at: str | None
    saved_playlist_id: int | None


@dataclass(frozen=True)
class GeneratedMixItem:
    mix_id: str
    position: int
    track_id: int
    score: float | None
    score_breakdown_json: str | None
    reason_json: str | None
    created_at: str


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


PLAYBACK_SOURCE_TYPES = {
    "release",
    "artist",
    "track",
    "playlist",
    "search",
    "flow",
    "autoplay",
    "manual",
    "generated_mix",
}
PLAYBACK_MODES = {"linear", "shuffle", "radio", "flow", "autoplay"}
PLAYBACK_SESSION_STATUSES = {"active", "paused", "ended"}
PLAYBACK_REPEAT_MODES = {"off", "one", "all"}
QUEUE_ORIGINS = {"source", "manual", "autoplay", "flow", "generated_mix"}
QUEUE_STATUSES = {"queued", "playing", "played", "skipped", "removed"}
GENERATED_MIX_TYPES = {"taste_region", "supermix", "forgotten", "discovery", "manual_seed", "debug"}
GENERATED_MIX_STATUSES = {"active", "stale", "saved", "archived"}
PLAYBACK_EVENT_TYPES = {
    "track_started",
    "progress",
    "play_threshold_reached",
    "completed",
    "skipped",
    "queue_click",
    "liked",
    "unliked",
    "disliked",
    "replayed",
    "removed_from_queue",
    "saved_to_playlist",
    "autoplay_toggled",
    "preference_changed",
}
MEANINGFUL_LISTEN_SECONDS = 30.0
MEANINGFUL_LISTEN_FRACTION = 0.5
EARLY_SKIP_SECONDS = 30.0
EARLY_SKIP_FRACTION = 0.25
LATE_SKIP_FRACTION = 0.70
COMPLETION_FRACTION = 0.90


class Store:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level="IMMEDIATE")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA wal_autocheckpoint = 1000")
        return conn

    def init(self) -> None:
        resolved_path = self.db_path.resolve()
        with INIT_LOCK:
            if resolved_path in INITIALIZED_DB_PATHS:
                return
            self._init_schema()
            INITIALIZED_DB_PATHS.add(resolved_path)

    def _init_schema(self) -> None:
        with self.connect() as conn:
            try:
                conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.OperationalError:
                logger.debug("Could not enable SQLite WAL mode for %s", self.db_path, exc_info=True)
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tracks (
                    id INTEGER PRIMARY KEY,
                    path TEXT UNIQUE NOT NULL,
                    artist TEXT,
                    title TEXT,
                    album TEXT,
                    genre TEXT,
                    year INTEGER,
                    duration REAL,
                    file_size INTEGER NOT NULL,
                    mtime INTEGER NOT NULL,
                    audio_hash TEXT,
                    missing_at TEXT,
                    last_seen_at TEXT,
                    added_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS embeddings (
                    track_id INTEGER NOT NULL,
                    model_name TEXT NOT NULL,
                    dim INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    vector_norm REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (track_id, model_name),
                    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS track_predictions (
                    track_id INTEGER NOT NULL,
                    model_name TEXT NOT NULL,
                    label TEXT NOT NULL,
                    score REAL NOT NULL,
                    rank INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (track_id, model_name, label),
                    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS track_model_outputs (
                    track_id INTEGER NOT NULL,
                    model_name TEXT NOT NULL,
                    dim INTEGER NOT NULL,
                    dtype TEXT NOT NULL,
                    aggregation TEXT NOT NULL,
                    scores_blob BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (track_id, model_name),
                    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS track_features (
                    track_id INTEGER NOT NULL,
                    feature_name TEXT NOT NULL,
                    value REAL,
                    text_value TEXT,
                    unit TEXT,
                    confidence REAL,
                    extractor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (track_id, feature_name, extractor),
                    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS scan_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS feedback (
                    seed_track_id INTEGER NOT NULL,
                    result_track_id INTEGER NOT NULL,
                    model_name TEXT NOT NULL,
                    rating INTEGER NOT NULL,
                    note TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (seed_track_id) REFERENCES tracks(id) ON DELETE CASCADE,
                    FOREIGN KEY (result_track_id) REFERENCES tracks(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS external_tracks (
                    provider TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    track_id INTEGER NOT NULL,
                    raw_json TEXT,
                    synced_at TEXT NOT NULL,
                    PRIMARY KEY (provider, external_id),
                    UNIQUE (provider, track_id),
                    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS artists (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    sort_name TEXT,
                    normalized_name TEXT NOT NULL UNIQUE,
                    image_url TEXT,
                    bio TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_artists_name ON artists(name);

                CREATE TABLE IF NOT EXISTS artist_aliases (
                    artist_id INTEGER NOT NULL,
                    alias TEXT NOT NULL,
                    normalized_alias TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (normalized_alias, source),
                    FOREIGN KEY (artist_id) REFERENCES artists(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_artist_aliases_artist
                    ON artist_aliases(artist_id);

                CREATE TABLE IF NOT EXISTS releases (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    normalized_title TEXT NOT NULL,
                    release_type TEXT NOT NULL DEFAULT 'unknown',
                    release_date TEXT,
                    release_year INTEGER,
                    cover_art_id TEXT,
                    track_count INTEGER NOT NULL DEFAULT 0,
                    duration REAL,
                    label TEXT,
                    catalog_number TEXT,
                    identity_key TEXT NOT NULL UNIQUE,
                    identity_confidence TEXT NOT NULL DEFAULT 'derived',
                    added_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_releases_normalized_title
                    ON releases(normalized_title);
                CREATE INDEX IF NOT EXISTS idx_releases_year ON releases(release_year);
                CREATE INDEX IF NOT EXISTS idx_releases_type ON releases(release_type);

                CREATE TABLE IF NOT EXISTS release_tracks (
                    release_id INTEGER NOT NULL,
                    track_id INTEGER NOT NULL,
                    disc_number INTEGER,
                    track_number INTEGER,
                    position INTEGER NOT NULL,
                    title_override TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (release_id, track_id),
                    UNIQUE (release_id, position),
                    FOREIGN KEY (release_id) REFERENCES releases(id) ON DELETE CASCADE,
                    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_release_tracks_track
                    ON release_tracks(track_id);

                CREATE TABLE IF NOT EXISTS track_artists (
                    track_id INTEGER NOT NULL,
                    artist_id INTEGER NOT NULL,
                    role TEXT NOT NULL DEFAULT 'primary',
                    position INTEGER NOT NULL DEFAULT 0,
                    credit_text TEXT,
                    confidence TEXT NOT NULL DEFAULT 'derived',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (track_id, artist_id, role, position),
                    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE,
                    FOREIGN KEY (artist_id) REFERENCES artists(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_track_artists_artist_role
                    ON track_artists(artist_id, role);
                CREATE INDEX IF NOT EXISTS idx_track_artists_track
                    ON track_artists(track_id);

                CREATE TABLE IF NOT EXISTS release_artists (
                    release_id INTEGER NOT NULL,
                    artist_id INTEGER NOT NULL,
                    role TEXT NOT NULL DEFAULT 'primary',
                    position INTEGER NOT NULL DEFAULT 0,
                    credit_text TEXT,
                    confidence TEXT NOT NULL DEFAULT 'derived',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (release_id, artist_id, role, position),
                    FOREIGN KEY (release_id) REFERENCES releases(id) ON DELETE CASCADE,
                    FOREIGN KEY (artist_id) REFERENCES artists(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_release_artists_artist_role
                    ON release_artists(artist_id, role);
                CREATE INDEX IF NOT EXISTS idx_release_artists_release
                    ON release_artists(release_id);

                CREATE TABLE IF NOT EXISTS external_ids (
                    provider TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id INTEGER NOT NULL,
                    external_id TEXT NOT NULL,
                    raw_json TEXT,
                    synced_at TEXT NOT NULL,
                    PRIMARY KEY (provider, entity_type, external_id)
                );

                CREATE INDEX IF NOT EXISTS idx_external_ids_entity
                    ON external_ids(entity_type, entity_id);

                CREATE TABLE IF NOT EXISTS playback_sessions (
                    id TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL,
                    source_id INTEGER,
                    source_label TEXT,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_track_id INTEGER,
                    current_queue_item_id TEXT,
                    autoplay_enabled INTEGER NOT NULL DEFAULT 0,
                    shuffle_enabled INTEGER NOT NULL DEFAULT 0,
                    repeat_mode TEXT NOT NULL DEFAULT 'off',
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    ended_at TEXT,
                    settings_json TEXT,
                    state_json TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_playback_sessions_status_updated
                    ON playback_sessions(status, updated_at);

                CREATE TABLE IF NOT EXISTS queue_items (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    track_id INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    origin TEXT NOT NULL,
                    source_type TEXT,
                    source_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'queued',
                    locked INTEGER NOT NULL DEFAULT 0,
                    reason TEXT,
                    score REAL,
                    debug_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES playback_sessions(id) ON DELETE CASCADE
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_items_session_position
                    ON queue_items(session_id, position);
                CREATE INDEX IF NOT EXISTS idx_queue_items_session_status
                    ON queue_items(session_id, status, position);
                CREATE INDEX IF NOT EXISTS idx_queue_items_track
                    ON queue_items(track_id);

                CREATE TABLE IF NOT EXISTS playback_events (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    queue_item_id TEXT,
                    track_id INTEGER,
                    release_id INTEGER,
                    artist_id INTEGER,
                    event_type TEXT NOT NULL,
                    position_seconds REAL,
                    duration_seconds REAL,
                    play_fraction REAL,
                    created_at TEXT NOT NULL,
                    client_event_id TEXT,
                    source TEXT NOT NULL DEFAULT 'web',
                    payload_json TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_playback_events_session_created
                    ON playback_events(session_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_playback_events_track_created
                    ON playback_events(track_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_playback_events_type_created
                    ON playback_events(event_type, created_at);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_playback_events_client_event_id
                    ON playback_events(client_event_id)
                    WHERE client_event_id IS NOT NULL;

                CREATE TABLE IF NOT EXISTS user_track_preferences (
                    track_id INTEGER PRIMARY KEY,
                    liked INTEGER NOT NULL DEFAULT 0,
                    disliked INTEGER NOT NULL DEFAULT 0,
                    play_count INTEGER NOT NULL DEFAULT 0,
                    completion_count INTEGER NOT NULL DEFAULT 0,
                    skip_count INTEGER NOT NULL DEFAULT 0,
                    early_skip_count INTEGER NOT NULL DEFAULT 0,
                    replay_count INTEGER NOT NULL DEFAULT 0,
                    last_played_at TEXT,
                    last_completed_at TEXT,
                    last_skipped_at TEXT,
                    score REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_release_preferences (
                    release_id INTEGER PRIMARY KEY,
                    liked INTEGER NOT NULL DEFAULT 0,
                    play_count INTEGER NOT NULL DEFAULT 0,
                    completion_count INTEGER NOT NULL DEFAULT 0,
                    skip_count INTEGER NOT NULL DEFAULT 0,
                    last_played_at TEXT,
                    last_completed_at TEXT,
                    score REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_artist_preferences (
                    artist_id INTEGER PRIMARY KEY,
                    liked INTEGER NOT NULL DEFAULT 0,
                    play_count INTEGER NOT NULL DEFAULT 0,
                    completion_count INTEGER NOT NULL DEFAULT 0,
                    skip_count INTEGER NOT NULL DEFAULT 0,
                    last_played_at TEXT,
                    score REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS generated_mixes (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    mix_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    anchor_json TEXT,
                    settings_json TEXT,
                    score_summary_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT,
                    saved_playlist_id INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_generated_mixes_status_updated
                    ON generated_mixes(status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS generated_mix_items (
                    mix_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    track_id INTEGER NOT NULL,
                    score REAL,
                    score_breakdown_json TEXT,
                    reason_json TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (mix_id, position),
                    UNIQUE (mix_id, track_id),
                    FOREIGN KEY (mix_id) REFERENCES generated_mixes(id) ON DELETE CASCADE,
                    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_generated_mix_items_track
                    ON generated_mix_items(track_id);

                CREATE TABLE IF NOT EXISTS instant_mix_requests (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    seed_item_id TEXT NOT NULL,
                    seed_track_id INTEGER,
                    model_name TEXT NOT NULL,
                    requested_count INTEGER,
                    effective_count INTEGER NOT NULL,
                    max_per_artist INTEGER NOT NULL,
                    exclude_same_album INTEGER NOT NULL,
                    min_similarity REAL,
                    status TEXT NOT NULL,
                    result_count INTEGER NOT NULL DEFAULT 0,
                    skipped_without_external_id INTEGER NOT NULL DEFAULT 0,
                    duration_ms REAL,
                    error TEXT,
                    params_json TEXT NOT NULL,
                    results_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (seed_track_id) REFERENCES tracks(id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_instant_mix_requests_created_at
                    ON instant_mix_requests(created_at DESC);

                CREATE TABLE IF NOT EXISTS analysis_jobs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL DEFAULT 'analyze',
                    model_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    total INTEGER NOT NULL DEFAULT 0,
                    progress_done INTEGER NOT NULL DEFAULT 0,
                    progress_failed INTEGER NOT NULL DEFAULT 0,
                    local_executor_enabled INTEGER NOT NULL DEFAULT 1,
                    workers INTEGER NOT NULL DEFAULT 1,
                    tf_threads INTEGER NOT NULL DEFAULT 1,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS analysis_tasks (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    track_id INTEGER NOT NULL,
                    model_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    path TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    mtime INTEGER NOT NULL,
                    error TEXT,
                    error_type TEXT,
                    stage TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY (job_id) REFERENCES analysis_jobs(id) ON DELETE CASCADE,
                    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE,
                    UNIQUE (job_id, track_id, model_name)
                );

                CREATE INDEX IF NOT EXISTS idx_analysis_tasks_claim
                    ON analysis_tasks(status, model_name, lease_expires_at);
                CREATE INDEX IF NOT EXISTS idx_analysis_tasks_job
                    ON analysis_tasks(job_id, status);
                CREATE INDEX IF NOT EXISTS idx_track_predictions_model_label_score
                    ON track_predictions(model_name, label, score DESC, track_id);
                CREATE INDEX IF NOT EXISTS idx_track_predictions_model_rank_score
                    ON track_predictions(model_name, rank, score DESC, track_id);
                CREATE INDEX IF NOT EXISTS idx_track_predictions_track_model_rank
                    ON track_predictions(track_id, model_name, rank);
                CREATE INDEX IF NOT EXISTS idx_track_model_outputs_model_track
                    ON track_model_outputs(model_name, track_id);
                CREATE INDEX IF NOT EXISTS idx_track_features_name_value
                    ON track_features(extractor, feature_name, value, track_id);
                CREATE INDEX IF NOT EXISTS idx_track_features_name_text
                    ON track_features(extractor, feature_name, text_value, track_id);
                CREATE INDEX IF NOT EXISTS idx_track_features_extractor_track
                    ON track_features(extractor, track_id);

                CREATE TABLE IF NOT EXISTS analysis_workers (
                    worker_id TEXT PRIMARY KEY,
                    models TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    claimed_count INTEGER NOT NULL DEFAULT 0,
                    completed_count INTEGER NOT NULL DEFAULT 0,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    released_count INTEGER NOT NULL DEFAULT 0,
                    current_task_id TEXT,
                    stage TEXT,
                    message TEXT
                );
                """
            )
            self._ensure_column(conn, "tracks", "genre", "TEXT")
            self._ensure_column(conn, "tracks", "year", "INTEGER")
            self._ensure_column(conn, "tracks", "missing_at", "TEXT")
            self._ensure_column(conn, "tracks", "last_seen_at", "TEXT")
            self._ensure_column(conn, "tracks", "added_at", "TEXT")
            self._ensure_column(conn, "releases", "added_at", "TEXT")
            self._ensure_column(conn, "analysis_jobs", "kind", "TEXT NOT NULL DEFAULT 'analyze'")
            self._ensure_column(conn, "analysis_jobs", "progress_done", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "analysis_jobs", "progress_failed", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "analysis_workers", "claimed_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "analysis_workers", "completed_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "analysis_workers", "failed_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "analysis_workers", "released_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "analysis_workers", "current_task_id", "TEXT")
            self._ensure_column(conn, "analysis_workers", "stage", "TEXT")
            self._ensure_column(conn, "analysis_workers", "message", "TEXT")
            self._ensure_column(conn, "playback_sessions", "source_label", "TEXT")
            self._ensure_column(conn, "playback_sessions", "current_queue_item_id", "TEXT")
            self._ensure_column(conn, "playback_sessions", "settings_json", "TEXT")
            self._ensure_column(conn, "playback_sessions", "state_json", "TEXT")
            self._ensure_column(conn, "queue_items", "locked", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "queue_items", "reason", "TEXT")
            self._ensure_column(conn, "queue_items", "score", "REAL")
            self._ensure_column(conn, "queue_items", "debug_json", "TEXT")
            self._ensure_column(conn, "playback_events", "release_id", "INTEGER")
            self._ensure_column(conn, "playback_events", "artist_id", "INTEGER")
            self._ensure_column(conn, "playback_events", "source", "TEXT NOT NULL DEFAULT 'web'")
            self._ensure_column(conn, "playback_events", "payload_json", "TEXT")
            self._backfill_added_timestamps(conn)

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        declaration: str,
    ) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    def _backfill_added_timestamps(self, conn: sqlite3.Connection) -> None:
        conn.execute("UPDATE tracks SET added_at = created_at WHERE added_at IS NULL")
        conn.execute(
            """
            UPDATE releases
            SET added_at = COALESCE(
                (
                    SELECT MAX(COALESCE(t.added_at, t.created_at))
                    FROM release_tracks rt
                    JOIN tracks t ON t.id = rt.track_id
                    WHERE rt.release_id = releases.id
                ),
                added_at,
                created_at
            )
            WHERE added_at IS NULL
            """
        )

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
                SET title = ?, normalized_title = ?, release_type = ?,
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

        rows = conn.execute(
            """
            SELECT
                ta.artist_id,
                ta.role,
                MIN(ta.credit_text) AS credit_text,
                MIN(COALESCE(rt.position, rt.track_id)) AS release_position,
                MIN(ta.position) AS artist_position,
                MIN(a.name) AS artist_name
            FROM release_tracks rt
            JOIN track_artists ta ON ta.track_id = rt.track_id
            JOIN artists a ON a.id = ta.artist_id
            WHERE rt.release_id = ? AND ta.role = 'primary'
            GROUP BY ta.artist_id, ta.role
            ORDER BY release_position, artist_position, artist_name
            """,
            (release_id,),
        ).fetchall()
        conn.execute("DELETE FROM release_artists WHERE release_id = ?", (release_id,))
        for position, row in enumerate(rows):
            credit = ArtistCredit(
                name=str(row["artist_name"]),
                role=str(row["role"]),
                position=position,
                credit_text=row["credit_text"],
                confidence="derived",
            )
            self._insert_release_artist(conn, release_id, int(row["artist_id"]), credit, now)

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
                    id, source_type, source_id, source_label, mode, status,
                    autoplay_enabled, shuffle_enabled, repeat_mode, started_at,
                    updated_at, ended_at, settings_json, state_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
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
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM playback_sessions WHERE id = ?",
                (session_id,),
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
        params.append(session_id)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE playback_sessions SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            row = conn.execute(
                "SELECT * FROM playback_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        return row_to_playback_session(row) if row else None

    def list_queue_items(self, session_id: str, include_removed: bool = False) -> list[QueueItem]:
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
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM queue_items WHERE id = ?",
                (queue_item_id,),
            ).fetchone()
        return row_to_queue_item(row) if row else None

    def replace_queue_items(
        self,
        session_id: str,
        items: list[dict[str, object]],
    ) -> list[QueueItem]:
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
                "UPDATE playback_sessions SET updated_at = ? WHERE id = ?",
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
                "UPDATE playback_sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            row = conn.execute(
                "SELECT * FROM queue_items WHERE id = ? AND session_id = ?",
                (queue_item_id, session_id),
            ).fetchone()
        return row_to_queue_item(row) if row else None

    def move_queue_item(self, session_id: str, queue_item_id: str, position: int) -> list[QueueItem]:
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
                "UPDATE playback_sessions SET updated_at = ? WHERE id = ?",
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

    def record_playback_event(
        self,
        *,
        event_type: str,
        session_id: str | None = None,
        queue_item_id: str | None = None,
        track_id: int | None = None,
        release_id: int | None = None,
        artist_id: int | None = None,
        position_seconds: float | None = None,
        duration_seconds: float | None = None,
        play_fraction: float | None = None,
        client_event_id: str | None = None,
        source: str = "web",
        payload: dict[str, object] | None = None,
        event_id: str | None = None,
        created_at: str | None = None,
    ) -> PlaybackEventResult:
        _require_choice(event_type, PLAYBACK_EVENT_TYPES, "event_type")
        created_at = created_at or utc_now()
        event_id = event_id or str(uuid4())
        play_fraction = _normalized_play_fraction(
            play_fraction,
            position_seconds=position_seconds,
            duration_seconds=duration_seconds,
        )
        payload_json = _json_dumps(payload)
        with self.connect() as conn:
            if client_event_id:
                duplicate = conn.execute(
                    "SELECT * FROM playback_events WHERE client_event_id = ?",
                    (client_event_id,),
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
            if track_id is not None:
                release_id = release_id or _release_id_for_track(conn, track_id)
                artist_id = artist_id or _primary_artist_id_for_track(conn, track_id)
            conn.execute(
                """
                INSERT INTO playback_events (
                    id, session_id, queue_item_id, track_id, release_id, artist_id,
                    event_type, position_seconds, duration_seconds, play_fraction,
                    created_at, client_event_id, source, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    session_id,
                    queue_item_id,
                    track_id,
                    release_id,
                    artist_id,
                    event_type,
                    position_seconds,
                    duration_seconds,
                    play_fraction,
                    created_at,
                    client_event_id,
                    source or "web",
                    payload_json,
                ),
            )
            event_row = conn.execute(
                "SELECT * FROM playback_events WHERE id = ?",
                (event_id,),
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
        if event_type in {"progress", "queue_click", "autoplay_toggled"}:
            return {}
        track_id = int(row["track_id"]) if row["track_id"] is not None else None
        release_id = int(row["release_id"]) if row["release_id"] is not None else None
        artist_ids = _artist_ids_for_event(conn, row)
        created_at = str(row["created_at"])
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
            "SELECT * FROM user_track_preferences WHERE track_id = ?",
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
        if event_type == "track_started":
            conn.execute(
                """
                UPDATE user_track_preferences
                SET last_played_at = ?, updated_at = ?
                WHERE track_id = ?
                """,
                (created_at, created_at, track_id),
            )
        elif event_type == "play_threshold_reached":
            conn.execute(
                """
                UPDATE user_track_preferences
                SET play_count = play_count + 1,
                    last_played_at = ?,
                    score = score + 0.25,
                    updated_at = ?
                WHERE track_id = ?
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
                WHERE track_id = ?
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
                WHERE track_id = ?
                """,
                (1 if early else 0, created_at, score_delta, created_at, track_id),
            )
        elif event_type == "liked":
            conn.execute(
                """
                UPDATE user_track_preferences
                SET liked = 1, disliked = 0, score = score + CASE WHEN liked = 0 THEN 5.0 ELSE 0 END
                    + CASE WHEN disliked = 1 THEN 5.0 ELSE 0 END, updated_at = ?
                WHERE track_id = ?
                """,
                (created_at, track_id),
            )
        elif event_type == "unliked":
            conn.execute(
                """
                UPDATE user_track_preferences
                SET liked = 0, score = score - CASE WHEN liked = 1 THEN 5.0 ELSE 0 END,
                    updated_at = ?
                WHERE track_id = ?
                """,
                (created_at, track_id),
            )
        elif event_type == "disliked":
            conn.execute(
                """
                UPDATE user_track_preferences
                SET disliked = 1, liked = 0, score = score - CASE WHEN disliked = 0 THEN 5.0 ELSE 0 END
                    - CASE WHEN liked = 1 THEN 5.0 ELSE 0 END, updated_at = ?
                WHERE track_id = ?
                """,
                (created_at, track_id),
            )
        elif event_type == "replayed":
            conn.execute(
                """
                UPDATE user_track_preferences
                SET replay_count = replay_count + 1, score = score + 2.0, updated_at = ?
                WHERE track_id = ?
                """,
                (created_at, track_id),
            )
        elif event_type == "saved_to_playlist":
            conn.execute(
                """
                UPDATE user_track_preferences
                SET score = score + 3.0, updated_at = ?
                WHERE track_id = ?
                """,
                (created_at, track_id),
            )
        elif event_type == "removed_from_queue":
            conn.execute(
                """
                UPDATE user_track_preferences
                SET score = score - 1.0, updated_at = ?
                WHERE track_id = ?
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
                WHERE release_id = ?
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
                WHERE release_id = ?
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
                WHERE release_id = ?
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
                WHERE release_id = ?
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
                WHERE artist_id = ?
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
                WHERE artist_id = ?
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
                WHERE artist_id = ?
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
                WHERE artist_id = ?
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
            INSERT OR IGNORE INTO user_track_preferences (track_id, updated_at)
            VALUES (?, ?)
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
            INSERT OR IGNORE INTO user_release_preferences (release_id, updated_at)
            VALUES (?, ?)
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
            INSERT OR IGNORE INTO user_artist_preferences (artist_id, updated_at)
            VALUES (?, ?)
            """,
            (artist_id, updated_at),
        )

    def get_track_preference(self, track_id: int) -> UserTrackPreference | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_track_preferences WHERE track_id = ?",
                (track_id,),
            ).fetchone()
        return row_to_user_track_preference(row) if row else None

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
            WHERE track_id = ?
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
                WHERE release_id = ?
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
                WHERE artist_id = ?
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
                "SELECT * FROM user_release_preferences WHERE release_id = ?",
                (release_id,),
            ).fetchone()
        return row_to_user_release_preference(row) if row else None

    def get_artist_preference(self, artist_id: int) -> UserArtistPreference | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_artist_preferences WHERE artist_id = ?",
                (artist_id,),
            ).fetchone()
        return row_to_user_artist_preference(row) if row else None

    def list_playback_events(self, session_id: str | None = None) -> list[PlaybackEvent]:
        where = ""
        params: list[object] = []
        if session_id is not None:
            where = "WHERE session_id = ?"
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
                    id, title, mix_type, status, anchor_json, settings_json,
                    score_summary_json, created_at, updated_at, expires_at,
                    saved_playlist_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    mix_type = excluded.mix_type,
                    status = excluded.status,
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
                ORDER BY updated_at DESC, created_at DESC, id
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
            conn.execute(
                """
                UPDATE generated_mixes
                SET status = 'saved', updated_at = ?
                WHERE id = ? AND status != 'archived'
                """,
                (now, mix_id),
            )
            row = conn.execute(
                "SELECT * FROM generated_mixes WHERE id = ?",
                (mix_id,),
            ).fetchone()
        return row_to_generated_mix(row) if row else None

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
            rows = conn.execute("SELECT id, path FROM tracks ORDER BY id").fetchall()
            for row in rows:
                checked += 1
                track_id = int(row["id"])
                path = Path(str(row["path"]))
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
                "count DESC, value",
            ),
            "years": facet_rows("year", "t.year IS NOT NULL", "value DESC"),
            "artists": facet_rows(
                "artist",
                "t.artist IS NOT NULL AND TRIM(t.artist) != ''",
                "count DESC, value",
            ),
            "albums": facet_rows(
                "album",
                "t.album IS NOT NULL AND TRIM(t.album) != ''",
                "count DESC, value",
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

    def create_analysis_job(
        self,
        model_name: str,
        limit: int | None,
        *,
        kind: str = "analyze",
        tracks: list[Track] | None = None,
        local_executor_enabled: bool = True,
        workers: int = 1,
        tf_threads: int = 1,
        max_attempts: int = 3,
        job_id: str | None = None,
    ) -> AnalysisJob:
        now = utc_now()
        job_id = job_id or str(uuid4())
        tracks = tracks if tracks is not None else self.list_tracks_missing_embedding(model_name, limit=limit)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO analysis_jobs (
                    id, kind, model_name, status, total, local_executor_enabled, workers,
                    tf_threads, max_attempts, message, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    kind,
                    model_name,
                    "running" if tracks else "completed",
                    len(tracks),
                    int(local_executor_enabled),
                    int(workers),
                    int(tf_threads),
                    int(max_attempts),
                    f"Queued {len(tracks)} tracks for {model_name}",
                    now,
                    now,
                ),
            )
            conn.executemany(
                """
                INSERT INTO analysis_tasks (
                    id, job_id, track_id, model_name, status, max_attempts, path,
                    file_size, mtime, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(uuid4()),
                        job_id,
                        track.id,
                        model_name,
                        int(max_attempts),
                        track.path,
                        track.file_size,
                        track.mtime,
                        now,
                        now,
                    )
                    for track in tracks
                ],
            )
            if not tracks:
                conn.execute(
                    "UPDATE analysis_jobs SET finished_at = ?, message = ? WHERE id = ?",
                    (now, f"Analyzed 0 tracks, failed 0", job_id),
                )
        return self.get_analysis_job(job_id)  # type: ignore[return-value]

    def create_progress_job(
        self,
        kind: str,
        model_name: str,
        *,
        total: int = 0,
        message: str = "",
        job_id: str | None = None,
    ) -> AnalysisJob:
        now = utc_now()
        job_id = job_id or str(uuid4())
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO analysis_jobs (
                    id, kind, model_name, status, total, progress_done,
                    progress_failed, local_executor_enabled, workers, tf_threads,
                    max_attempts, message, created_at, updated_at
                )
                VALUES (?, ?, ?, 'running', ?, 0, 0, 0, 0, 0, 1, ?, ?, ?)
                """,
                (
                    job_id,
                    kind,
                    model_name,
                    max(int(total), 0),
                    message or f"Running {kind}",
                    now,
                    now,
                ),
            )
        return self._get_analysis_job_no_refresh(job_id)  # type: ignore[return-value]

    def update_progress_job(
        self,
        job_id: str,
        *,
        done: int | None = None,
        failed: int | None = None,
        total: int | None = None,
        status: str | None = None,
        message: str | None = None,
        finished: bool = False,
    ) -> AnalysisJob | None:
        now = utc_now()
        updates = ["updated_at = ?"]
        params: list[object] = [now]
        if done is not None:
            updates.append("progress_done = ?")
            params.append(max(int(done), 0))
        if failed is not None:
            updates.append("progress_failed = ?")
            params.append(max(int(failed), 0))
        if total is not None:
            updates.append("total = ?")
            params.append(max(int(total), 0))
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if message is not None:
            updates.append("message = ?")
            params.append(message)
        if finished:
            updates.append("finished_at = ?")
            params.append(now)
        params.append(job_id)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE analysis_jobs SET {', '.join(updates)} WHERE id = ?",
                params,
            )
        return self._get_analysis_job_no_refresh(job_id)

    def expire_analysis_leases(self, now: str | None = None) -> int:
        now = now or utc_now()
        with self.connect() as conn:
            job_rows = conn.execute(
                """
                SELECT DISTINCT job_id
                FROM analysis_tasks
                WHERE status = 'leased'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at < ?
                """,
                (now,),
            ).fetchall()
            cursor = conn.execute(
                """
                UPDATE analysis_tasks
                SET status = 'queued',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    stage = 'lease_expired',
                    updated_at = ?
                WHERE status = 'leased'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at < ?
                """,
                (now, now),
            )
            expired = int(cursor.rowcount)
        for row in job_rows:
            self.refresh_analysis_job(str(row["job_id"]))
        return expired

    def register_analysis_worker(self, worker_id: str, models: list[str]) -> None:
        now = utc_now()
        models_text = ",".join(sorted(set(models)))
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO analysis_workers (worker_id, models, status, last_seen_at, created_at)
                VALUES (?, ?, 'online', ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    models = excluded.models,
                    status = 'online',
                    last_seen_at = excluded.last_seen_at,
                    stage = COALESCE(analysis_workers.stage, 'heartbeat')
                """,
                (worker_id, models_text, now, now),
            )

    def heartbeat_analysis_worker(
        self,
        worker_id: str,
        models: list[str],
        *,
        min_interval_seconds: int = 60,
    ) -> bool:
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        models_text = ",".join(sorted(set(models)))
        with self.connect() as conn:
            row = conn.execute(
                "SELECT models, last_seen_at FROM analysis_workers WHERE worker_id = ?",
                (worker_id,),
            ).fetchone()
            if row is not None and row["models"] == models_text:
                try:
                    last_seen_at = datetime.fromisoformat(str(row["last_seen_at"]))
                except ValueError:
                    last_seen_at = None
                if last_seen_at is not None:
                    if last_seen_at.tzinfo is None:
                        last_seen_at = last_seen_at.replace(tzinfo=UTC)
                    if now_dt - last_seen_at < timedelta(seconds=max(min_interval_seconds, 0)):
                        return False
            conn.execute(
                """
                INSERT INTO analysis_workers (worker_id, models, status, last_seen_at, created_at)
                VALUES (?, ?, 'online', ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    models = excluded.models,
                    status = 'online',
                    last_seen_at = excluded.last_seen_at,
                    stage = COALESCE(analysis_workers.stage, 'heartbeat')
                """,
                (worker_id, models_text, now, now),
            )
        return True

    def update_analysis_worker(
        self,
        worker_id: str,
        *,
        status: str = "online",
        stage: str | None = None,
        message: str | None = None,
        current_task_id: str | None = None,
        claimed_delta: int = 0,
        completed_delta: int = 0,
        failed_delta: int = 0,
        released_delta: int = 0,
    ) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE analysis_workers
                SET status = ?,
                    last_seen_at = ?,
                    stage = COALESCE(?, stage),
                    message = COALESCE(?, message),
                    current_task_id = ?,
                    claimed_count = claimed_count + ?,
                    completed_count = completed_count + ?,
                    failed_count = failed_count + ?,
                    released_count = released_count + ?
                WHERE worker_id = ?
                """,
                (
                    status,
                    now,
                    stage,
                    message,
                    current_task_id,
                    int(claimed_delta),
                    int(completed_delta),
                    int(failed_delta),
                    int(released_delta),
                    worker_id,
                ),
            )

    def list_analysis_workers(self) -> list[AnalysisWorker]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM analysis_workers ORDER BY last_seen_at DESC"
            ).fetchall()
        return [row_to_analysis_worker(row) for row in rows]

    def analysis_job_task_summary(self, job_id: str) -> dict[str, object]:
        with self.connect() as conn:
            status_rows = conn.execute(
                """
                SELECT status, stage, COUNT(*) AS count
                FROM analysis_tasks
                WHERE job_id = ?
                GROUP BY status, stage
                ORDER BY status, stage
                """,
                (job_id,),
            ).fetchall()
            leased_rows = conn.execute(
                """
                SELECT lease_owner, COUNT(*) AS count
                FROM analysis_tasks
                WHERE job_id = ?
                  AND status = 'leased'
                  AND lease_owner IS NOT NULL
                GROUP BY lease_owner
                ORDER BY count DESC, lease_owner
                """,
                (job_id,),
            ).fetchall()
            oldest_lease = conn.execute(
                """
                SELECT lease_owner, lease_expires_at, stage, updated_at, path
                FROM analysis_tasks
                WHERE job_id = ?
                  AND status = 'leased'
                ORDER BY lease_expires_at, updated_at
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            error_rows = conn.execute(
                """
                SELECT id, track_id, status, error, error_type, stage, updated_at, lease_owner, path
                FROM analysis_tasks
                WHERE job_id = ?
                  AND error IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT 5
                """,
                (job_id,),
            ).fetchall()
        return {
            "status_breakdown": [
                {
                    "status": str(row["status"]),
                    "stage": row["stage"],
                    "count": int(row["count"] or 0),
                }
                for row in status_rows
            ],
            "leased_workers": [
                {
                    "worker_id": str(row["lease_owner"]),
                    "count": int(row["count"] or 0),
                }
                for row in leased_rows
            ],
            "oldest_lease": (
                {
                    "worker_id": oldest_lease["lease_owner"],
                    "lease_expires_at": oldest_lease["lease_expires_at"],
                    "stage": oldest_lease["stage"],
                    "updated_at": oldest_lease["updated_at"],
                    "path": oldest_lease["path"],
                }
                if oldest_lease is not None
                else None
            ),
            "recent_errors": [
                {
                    "task_id": str(row["id"]),
                    "track_id": int(row["track_id"]),
                    "status": str(row["status"]),
                    "error": str(row["error"]),
                    "error_type": row["error_type"],
                    "stage": row["stage"],
                    "updated_at": row["updated_at"],
                    "worker_id": row["lease_owner"],
                    "path": row["path"],
                }
                for row in error_rows
            ],
        }

    def list_analysis_job_tasks(
        self,
        job_id: str,
        *,
        statuses: list[str] | None = None,
        limit: int = 100,
    ) -> list[AnalysisTask]:
        params: list[object] = [job_id]
        status_clause = ""
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            status_clause = f" AND status IN ({placeholders})"
            params.extend(statuses)
        params.append(int(limit))
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM analysis_tasks
                WHERE job_id = ?{status_clause}
                ORDER BY
                    CASE status
                        WHEN 'leased' THEN 0
                        WHEN 'queued' THEN 1
                        WHEN 'failed_retryable' THEN 2
                        WHEN 'final_failed' THEN 3
                        ELSE 4
                    END,
                    updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [row_to_analysis_task(row) for row in rows]

    def claim_analysis_tasks(
        self,
        worker_id: str,
        models: list[str],
        *,
        limit: int,
        lease_seconds: int = 300,
    ) -> list[AnalysisTask]:
        if not models or limit <= 0:
            return []
        self.expire_analysis_leases()
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        lease_expires_at = (now_dt + timedelta(seconds=lease_seconds)).isoformat()
        placeholders = ",".join("?" for _ in models)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                f"""
                SELECT t.* FROM analysis_tasks t
                JOIN analysis_jobs j ON j.id = t.job_id
                WHERE t.status = 'queued'
                  AND j.status = 'running'
                  AND t.model_name IN ({placeholders})
                ORDER BY t.created_at, t.track_id
                LIMIT ?
                """,
                [*models, int(limit)],
            ).fetchall()
            task_ids = [str(row["id"]) for row in rows]
            if task_ids:
                id_placeholders = ",".join("?" for _ in task_ids)
                conn.execute(
                    f"""
                    UPDATE analysis_tasks
                    SET status = 'leased',
                        attempts = attempts + 1,
                        lease_owner = ?,
                        lease_expires_at = ?,
                        error = NULL,
                        error_type = NULL,
                        stage = 'claimed',
                        updated_at = ?
                    WHERE id IN ({id_placeholders})
                    """,
                    [worker_id, lease_expires_at, now, *task_ids],
                )
        if task_ids:
            self.update_analysis_worker(
                worker_id,
                stage="claimed",
                message=f"claimed {len(task_ids)} task(s)",
                claimed_delta=len(task_ids),
            )
        return [
            row_to_analysis_task(
                {
                    **dict(row),
                    "status": "leased",
                    "attempts": int(row["attempts"]) + 1,
                    "lease_owner": worker_id,
                    "lease_expires_at": lease_expires_at,
                }
            )
            for row in rows
        ]

    def get_analysis_task(self, task_id: str) -> AnalysisTask | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM analysis_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        return row_to_analysis_task(row) if row else None

    def complete_analysis_task(
        self,
        task_id: str,
        worker_id: str | None = None,
        *,
        refresh_job: bool = True,
        update_worker: bool = True,
    ) -> None:
        now = utc_now()
        params: list[object] = [now, now, task_id]
        owner_clause = ""
        if worker_id is not None:
            owner_clause = " AND lease_owner = ?"
            params.append(worker_id)
        with self.connect() as conn:
            conn.execute(
                f"""
                UPDATE analysis_tasks
                SET status = 'completed',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    error = NULL,
                    error_type = NULL,
                    stage = 'completed',
                    updated_at = ?,
                    completed_at = ?
                WHERE id = ?{owner_clause}
                """,
                params,
            )
        if update_worker and worker_id is not None:
            self.update_analysis_worker(
                worker_id,
                stage="completed",
                completed_delta=1,
                current_task_id=None,
            )
        if not refresh_job:
            return
        task = self.get_analysis_task(task_id)
        if task is not None:
            self.refresh_analysis_job(task.job_id)

    def fail_analysis_task(
        self,
        task_id: str,
        *,
        error: str,
        error_type: str,
        stage: str,
        worker_id: str | None = None,
        retryable: bool = True,
    ) -> None:
        now = utc_now()
        task = self.get_analysis_task(task_id)
        if task is None:
            return
        if task.status == "final_failed" and task.stage == "cancelled":
            return
        status = "queued" if retryable and task.attempts < task.max_attempts else "final_failed"
        params: list[object] = [
            status,
            error,
            error_type,
            stage,
            now,
            task_id,
        ]
        owner_clause = ""
        if worker_id is not None:
            owner_clause = " AND lease_owner = ?"
            params.append(worker_id)
        with self.connect() as conn:
            conn.execute(
                f"""
                UPDATE analysis_tasks
                SET status = ?,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    error = ?,
                    error_type = ?,
                    stage = ?,
                    updated_at = ?
                WHERE id = ?{owner_clause}
                """,
                params,
            )
        if worker_id is not None:
            self.update_analysis_worker(
                worker_id,
                stage=stage,
                message=error[:240],
                failed_delta=1 if status == "final_failed" else 0,
                current_task_id=None,
            )
        self.refresh_analysis_job(task.job_id)

    def release_analysis_tasks(self, worker_id: str, task_ids: list[str] | None = None) -> int:
        now = utc_now()
        params: list[object] = [now, worker_id]
        task_clause = ""
        if task_ids:
            placeholders = ",".join("?" for _ in task_ids)
            task_clause = f" AND id IN ({placeholders})"
            params.extend(task_ids)
        with self.connect() as conn:
            job_rows = conn.execute(
                f"""
                SELECT DISTINCT job_id
                FROM analysis_tasks
                WHERE status = 'leased'
                  AND lease_owner = ?{task_clause}
                """,
                [worker_id, *(task_ids or [])],
            ).fetchall()
            cursor = conn.execute(
                f"""
                UPDATE analysis_tasks
                SET status = 'queued',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    stage = 'released',
                    updated_at = ?
                WHERE status = 'leased'
                  AND lease_owner = ?{task_clause}
                """,
                params,
            )
            released = int(cursor.rowcount)
        if released:
            self.update_analysis_worker(
                worker_id,
                stage="released",
                message=f"released {released} task(s)",
                released_delta=released,
                current_task_id=None,
            )
        for row in job_rows:
            self.refresh_analysis_job(str(row["job_id"]))
        return released

    def cancel_analysis_job(self, job_id: str, reason: str = "Cancelled by user") -> AnalysisJob | None:
        now = utc_now()
        with self.connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM analysis_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if exists is None:
                return None
            conn.execute(
                """
                UPDATE analysis_tasks
                SET status = 'final_failed',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    error = ?,
                    error_type = 'Cancelled',
                    stage = 'cancelled',
                    updated_at = ?
                WHERE job_id = ?
                  AND status IN ('queued', 'leased', 'failed_retryable')
                """,
                (reason, now, job_id),
            )
            counts = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS done,
                    SUM(CASE WHEN status = 'final_failed' THEN 1 ELSE 0 END) AS failed
                FROM analysis_tasks
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
            total = int(counts["total"] or 0)
            done = int(counts["done"] or 0)
            failed = int(counts["failed"] or 0)
            conn.execute(
                """
                UPDATE analysis_jobs
                SET status = 'cancelled',
                    total = ?,
                    message = ?,
                    updated_at = ?,
                    finished_at = ?
                WHERE id = ?
                """,
                (total, f"Cancelled: done {done}/{total}, failed {failed}", now, now, job_id),
            )
        return self._get_analysis_job_no_refresh(job_id)

    def refresh_analysis_job(self, job_id: str) -> AnalysisJob | None:
        now = utc_now()
        with self.connect() as conn:
            current = conn.execute(
                "SELECT kind, status, updated_at, finished_at FROM analysis_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if current is None:
                return None
            if not str(current["kind"]).startswith("analyze"):
                return self._get_analysis_job_no_refresh(job_id)
            if str(current["status"]) not in {"queued", "running"}:
                if current["finished_at"] is None:
                    conn.execute(
                        "UPDATE analysis_jobs SET finished_at = ? WHERE id = ?",
                        (current["updated_at"] or now, job_id),
                    )
                return self._get_analysis_job_no_refresh(job_id)
            counts = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS done,
                    SUM(CASE WHEN status = 'final_failed' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued,
                    SUM(CASE WHEN status = 'leased' THEN 1 ELSE 0 END) AS leased
                FROM analysis_tasks
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
            if counts is None:
                return None
            total = int(counts["total"] or 0)
            done = int(counts["done"] or 0)
            failed = int(counts["failed"] or 0)
            queued = int(counts["queued"] or 0)
            leased = int(counts["leased"] or 0)
            completed = done + failed >= total
            status = "completed" if completed else "running"
            message = f"Analyzed {done}/{total} tracks, failed {failed}"
            finished_at = now if completed else None
            conn.execute(
                """
                UPDATE analysis_jobs
                SET status = ?, total = ?, message = ?, updated_at = ?,
                    finished_at = COALESCE(finished_at, ?)
                WHERE id = ?
                """,
                (status, total, message, now, finished_at, job_id),
            )
        return self._get_analysis_job_no_refresh(job_id)

    def get_analysis_job(self, job_id: str) -> AnalysisJob | None:
        self.expire_analysis_leases()
        self.refresh_analysis_job_counts_only(job_id)
        return self._get_analysis_job_no_refresh(job_id)

    def get_analysis_job_status(self, job_id: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT status FROM analysis_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return str(row["status"]) if row else None

    def refresh_active_analysis_jobs(self) -> None:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id
                FROM analysis_jobs
                WHERE status IN ('queued', 'running')
                """
            ).fetchall()
        for row in rows:
            self.refresh_analysis_job(str(row["id"]))

    def has_active_analysis_job(self) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM analysis_jobs
                WHERE status IN ('queued', 'running')
                LIMIT 1
                """
            ).fetchone()
        return row is not None

    def _get_analysis_job_no_refresh(self, job_id: str) -> AnalysisJob | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT j.*,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'completed' THEN 1 ELSE 0 END)
                        ELSE j.progress_done
                    END AS done,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'final_failed' THEN 1 ELSE 0 END)
                        ELSE j.progress_failed
                    END AS failed,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'queued' THEN 1 ELSE 0 END)
                        ELSE 0
                    END AS queued,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'leased' THEN 1 ELSE 0 END)
                        ELSE 0
                    END AS leased,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'final_failed' THEN 1 ELSE 0 END)
                        ELSE j.progress_failed
                    END AS final_failed
                FROM analysis_jobs j
                LEFT JOIN analysis_tasks t ON t.job_id = j.id
                WHERE j.id = ?
                GROUP BY j.id
                """,
                (job_id,),
            ).fetchone()
        return row_to_analysis_job(row) if row else None

    def refresh_analysis_job_counts_only(self, job_id: str) -> None:
        with self.connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM analysis_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        if exists is not None:
            self.refresh_analysis_job(job_id)

    def recent_analysis_jobs(
        self,
        limit: int = 20,
        statuses: list[str] | None = None,
    ) -> list[AnalysisJob]:
        status_filter = ""
        params: list[object] = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            status_filter = f"WHERE status IN ({placeholders})"
            params.extend(statuses)
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT j.*,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'completed' THEN 1 ELSE 0 END)
                        ELSE j.progress_done
                    END AS done,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'final_failed' THEN 1 ELSE 0 END)
                        ELSE j.progress_failed
                    END AS failed,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'queued' THEN 1 ELSE 0 END)
                        ELSE 0
                    END AS queued,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'leased' THEN 1 ELSE 0 END)
                        ELSE 0
                    END AS leased,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'final_failed' THEN 1 ELSE 0 END)
                        ELSE j.progress_failed
                    END AS final_failed
                FROM (
                    SELECT *
                    FROM analysis_jobs
                    {status_filter}
                    ORDER BY created_at DESC
                    LIMIT ?
                ) j
                LEFT JOIN analysis_tasks t ON t.job_id = j.id
                GROUP BY j.id
                ORDER BY j.created_at DESC
                """,
                params,
            ).fetchall()
        return [row_to_analysis_job(row) for row in rows]

    def count_recent_finished_analysis_tasks(self, job_id: str, since: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM analysis_tasks
                WHERE job_id = ?
                  AND status IN ('completed', 'final_failed')
                  AND updated_at >= ?
                """,
                (job_id, since),
            ).fetchone()
        return int(row[0]) if row else 0

    def save_embedding(self, track_id: int, model_name: str, vector: np.ndarray) -> None:
        vector = np.asarray(vector, dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        now = utc_now()
        logger.debug(
            "Saving embedding track_id=%s model=%s dim=%s norm=%s",
            track_id,
            model_name,
            vector.shape[0],
            norm,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO embeddings (track_id, model_name, dim, vector, vector_norm, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(track_id, model_name) DO UPDATE SET
                    dim = excluded.dim,
                    vector = excluded.vector,
                    vector_norm = excluded.vector_norm,
                    created_at = excluded.created_at
                """,
                (track_id, model_name, int(vector.shape[0]), vector.tobytes(), norm, now),
            )

    def save_predictions(
        self,
        track_id: int,
        model_name: str,
        predictions: list[TrackPrediction],
    ) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM track_predictions WHERE track_id = ? AND model_name = ?",
                (track_id, model_name),
            )
            conn.executemany(
                """
                INSERT INTO track_predictions (
                    track_id, model_name, label, score, rank, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        track_id,
                        model_name,
                        prediction.label,
                        float(prediction.score),
                        int(prediction.rank),
                        now,
                    )
                    for prediction in predictions
                ],
            )

    def save_model_output(
        self,
        track_id: int,
        model_name: str,
        scores: np.ndarray,
        aggregation: str,
    ) -> None:
        vector = np.asarray(scores, dtype=np.float32).reshape(-1)
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO track_model_outputs (
                    track_id, model_name, dim, dtype, aggregation, scores_blob, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(track_id, model_name) DO UPDATE SET
                    dim = excluded.dim,
                    dtype = excluded.dtype,
                    aggregation = excluded.aggregation,
                    scores_blob = excluded.scores_blob,
                    created_at = excluded.created_at
                """,
                (
                    track_id,
                    model_name,
                    int(vector.shape[0]),
                    "float32",
                    aggregation,
                    vector.tobytes(),
                    now,
                ),
            )

    def load_model_output(self, track_id: int, model_name: str) -> TrackModelOutput | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT model_name, dim, dtype, aggregation, scores_blob
                FROM track_model_outputs
                WHERE track_id = ? AND model_name = ?
                """,
                (track_id, model_name),
            ).fetchone()
        if row is None:
            return None
        dtype = str(row["dtype"])
        if dtype != "float32":
            raise ValueError(f"Unsupported model output dtype: {dtype}")
        scores = np.frombuffer(
            row["scores_blob"],
            dtype=np.float32,
            count=int(row["dim"]),
        ).copy()
        return TrackModelOutput(
            model_name=str(row["model_name"]),
            scores=scores,
            aggregation=str(row["aggregation"]),
            dtype=dtype,
        )

    def list_model_outputs(self, track_id: int) -> list[TrackModelOutput]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT model_name, dim, dtype, aggregation, scores_blob
                FROM track_model_outputs
                WHERE track_id = ?
                ORDER BY model_name
                """,
                (track_id,),
            ).fetchall()
        outputs = []
        for row in rows:
            dtype = str(row["dtype"])
            if dtype != "float32":
                raise ValueError(f"Unsupported model output dtype: {dtype}")
            outputs.append(
                TrackModelOutput(
                    model_name=str(row["model_name"]),
                    scores=np.frombuffer(
                        row["scores_blob"],
                        dtype=np.float32,
                        count=int(row["dim"]),
                    ).copy(),
                    aggregation=str(row["aggregation"]),
                    dtype=dtype,
                )
            )
        return outputs

    def load_predictions(
        self,
        track_id: int,
        model_name: str,
        limit: int = 20,
    ) -> list[TrackPrediction]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT label, score, rank FROM track_predictions
                WHERE track_id = ? AND model_name = ?
                ORDER BY rank
                LIMIT ?
                """,
                (track_id, model_name, limit),
            ).fetchall()
        return [
            TrackPrediction(
                label=str(row["label"]),
                score=float(row["score"]),
                rank=int(row["rank"]),
            )
            for row in rows
        ]

    def list_predictions(self, track_id: int) -> dict[str, list[TrackPrediction]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT model_name, label, score, rank FROM track_predictions
                WHERE track_id = ?
                ORDER BY model_name, rank
                """,
                (track_id,),
            ).fetchall()
        predictions: dict[str, list[TrackPrediction]] = {}
        for row in rows:
            predictions.setdefault(str(row["model_name"]), []).append(
                TrackPrediction(
                    label=str(row["label"]),
                    score=float(row["score"]),
                    rank=int(row["rank"]),
                )
            )
        return predictions

    def count_predictions(self, model_name: str) -> int:
        with self.connect() as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(DISTINCT track_id) FROM track_predictions WHERE model_name = ?",
                    (model_name,),
                ).fetchone()[0]
            )

    def count_model_outputs(self, model_name: str | None = None) -> int:
        with self.connect() as conn:
            if model_name is None:
                return int(
                    conn.execute("SELECT COUNT(*) FROM track_model_outputs").fetchone()[0]
                )
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM track_model_outputs WHERE model_name = ?",
                    (model_name,),
                ).fetchone()[0]
            )

    def count_model_outputs_by_model(self, model_names: list[str]) -> dict[str, int]:
        if not model_names:
            return {}
        placeholders = ",".join("?" for _name in model_names)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT model_name, COUNT(*) AS count
                FROM track_model_outputs
                WHERE model_name IN ({placeholders})
                GROUP BY model_name
                """,
                model_names,
            ).fetchall()
        counts = {name: 0 for name in model_names}
        counts.update({str(row["model_name"]): int(row["count"]) for row in rows})
        return counts

    def count_tracks_missing_head_pack(self, model_names: list[str]) -> int:
        if not model_names:
            return 0
        placeholders = ",".join("?" for _name in model_names)
        with self.connect() as conn:
            return int(
                conn.execute(
                    f"""
                    SELECT COUNT(*) FROM tracks t
                    LEFT JOIN (
                        SELECT track_id, COUNT(DISTINCT model_name) AS model_count
                        FROM track_model_outputs
                        WHERE model_name IN ({placeholders})
                        GROUP BY track_id
                    ) o ON o.track_id = t.id
                    WHERE COALESCE(o.model_count, 0) < ?
                      AND t.missing_at IS NULL
                    """,
                    [*model_names, len(model_names)],
                ).fetchone()[0]
            )

    def list_tracks_missing_head_pack(
        self,
        model_names: list[str],
        limit: int | None = None,
    ) -> list[Track]:
        if not model_names:
            return []
        placeholders = ",".join("?" for _name in model_names)
        sql = f"""
            SELECT t.* FROM tracks t
            LEFT JOIN (
                SELECT track_id, COUNT(DISTINCT model_name) AS model_count
                FROM track_model_outputs
                WHERE model_name IN ({placeholders})
                GROUP BY track_id
            ) o ON o.track_id = t.id
            WHERE COALESCE(o.model_count, 0) < ?
              AND t.missing_at IS NULL
            ORDER BY t.id
        """
        params: list[object] = [*model_names, len(model_names)]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [row_to_track(row) for row in rows]

    def save_features(self, track_id: int, features: list[TrackFeature]) -> None:
        now = utc_now()
        with self.connect() as conn:
            extractors = sorted({feature.extractor for feature in features})
            for extractor in extractors:
                conn.execute(
                    "DELETE FROM track_features WHERE track_id = ? AND extractor = ?",
                    (track_id, extractor),
                )
            conn.executemany(
                """
                INSERT INTO track_features (
                    track_id, feature_name, value, text_value, unit, confidence,
                    extractor, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        track_id,
                        feature.name,
                        feature.value,
                        feature.text_value,
                        feature.unit,
                        feature.confidence,
                        feature.extractor,
                        now,
                    )
                    for feature in features
                ],
            )

    def delete_features_for_tracks(self, track_ids: list[int], extractor: str) -> int:
        ids = list(dict.fromkeys(track_ids))
        if not ids:
            return 0
        placeholders = ",".join("?" for _id in ids)
        with self.connect() as conn:
            cursor = conn.execute(
                f"""
                DELETE FROM track_features
                WHERE extractor = ?
                  AND track_id IN ({placeholders})
                """,
                [extractor, *ids],
            )
            return int(cursor.rowcount if cursor.rowcount is not None else 0)

    def load_features(self, track_id: int, extractor: str | None = None) -> list[TrackFeature]:
        sql = """
            SELECT feature_name, value, text_value, unit, confidence, extractor
            FROM track_features
            WHERE track_id = ?
        """
        params: list[object] = [track_id]
        if extractor is not None:
            sql += " AND extractor = ?"
            params.append(extractor)
        sql += " ORDER BY extractor, feature_name"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            TrackFeature(
                name=str(row["feature_name"]),
                value=float(row["value"]) if row["value"] is not None else None,
                text_value=row["text_value"],
                unit=row["unit"],
                confidence=float(row["confidence"]) if row["confidence"] is not None else None,
                extractor=str(row["extractor"]),
            )
            for row in rows
        ]

    def count_feature_tracks(self, extractor: str) -> int:
        with self.connect() as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(DISTINCT track_id) FROM track_features WHERE extractor = ?",
                    (extractor,),
                ).fetchone()[0]
            )

    def count_tracks_missing_features(self, extractor: str) -> int:
        with self.connect() as conn:
            active_tracks = int(
                conn.execute(
                    "SELECT COUNT(*) FROM tracks WHERE missing_at IS NULL"
                ).fetchone()[0]
            )
            complete_tracks = int(
                conn.execute(
                    """
                    SELECT COUNT(DISTINCT f.track_id)
                    FROM track_features f
                    JOIN tracks t ON t.id = f.track_id
                    WHERE f.extractor = ?
                      AND t.missing_at IS NULL
                    """,
                    (extractor,),
                ).fetchone()[0]
            )
        return max(active_tracks - complete_tracks, 0)

    def list_tracks_missing_features(
        self,
        extractor: str,
        limit: int | None = None,
    ) -> list[Track]:
        sql = """
            SELECT t.* FROM tracks t
            WHERE NOT EXISTS (
                SELECT 1 FROM track_features f
                WHERE f.track_id = t.id
                  AND f.extractor = ?
            )
              AND t.missing_at IS NULL
            ORDER BY t.id
        """
        params: list[object] = [extractor]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [row_to_track(row) for row in rows]

    def count_tracks_missing_predictions(self, model_name: str) -> int:
        with self.connect() as conn:
            return int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM tracks t
                    LEFT JOIN (
                        SELECT DISTINCT track_id FROM track_predictions
                        WHERE model_name = ?
                    ) p ON p.track_id = t.id
                    WHERE p.track_id IS NULL
                      AND t.missing_at IS NULL
                    """,
                    (model_name,),
                ).fetchone()[0]
            )

    def list_tracks_missing_predictions(
        self,
        model_name: str,
        limit: int | None = None,
    ) -> list[Track]:
        sql = """
            SELECT t.* FROM tracks t
            LEFT JOIN (
                SELECT DISTINCT track_id FROM track_predictions
                WHERE model_name = ?
            ) p ON p.track_id = t.id
            WHERE p.track_id IS NULL
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

    def load_embedding(self, track_id: int, model_name: str) -> np.ndarray | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT dim, vector FROM embeddings WHERE track_id = ? AND model_name = ?",
                (track_id, model_name),
            ).fetchone()
        if row is None:
            return None
        return np.frombuffer(row["vector"], dtype=np.float32, count=int(row["dim"])).copy()

    def load_embeddings(self, model_name: str) -> tuple[np.ndarray, np.ndarray]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT track_id, dim, vector FROM embeddings
                WHERE model_name = ?
                ORDER BY track_id
                """,
                (model_name,),
            ).fetchall()
        if not rows:
            return np.array([], dtype=np.int64), np.empty((0, 0), dtype=np.float32)
        dim = int(rows[0]["dim"])
        ids = np.array([int(row["track_id"]) for row in rows], dtype=np.int64)
        vectors = np.vstack(
            [np.frombuffer(row["vector"], dtype=np.float32, count=dim) for row in rows]
        ).astype(np.float32)
        return ids, vectors

    def count_tracks(self) -> int:
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0])

    def count_embeddings(self, model_name: str) -> int:
        with self.connect() as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM embeddings WHERE model_name = ?",
                    (model_name,),
                ).fetchone()[0]
            )

    def list_feature_summaries(self, extractor: str | None = None) -> list[FeatureSummary]:
        sql = """
            SELECT
                feature_name,
                extractor,
                COUNT(value) AS value_count,
                COUNT(text_value) AS text_count,
                COUNT(DISTINCT track_id) AS track_count,
                MIN(value) AS min_value,
                MAX(value) AS max_value,
                AVG(value) AS avg_value,
                MAX(unit) AS unit
            FROM track_features
        """
        params: list[object] = []
        if extractor is not None:
            sql += " WHERE extractor = ?"
            params.append(extractor)
        sql += " GROUP BY extractor, feature_name ORDER BY extractor, feature_name"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            FeatureSummary(
                name=str(row["feature_name"]),
                extractor=str(row["extractor"]),
                value_count=int(row["value_count"] or 0),
                text_count=int(row["text_count"] or 0),
                track_count=int(row["track_count"] or 0),
                min_value=float(row["min_value"]) if row["min_value"] is not None else None,
                max_value=float(row["max_value"]) if row["max_value"] is not None else None,
                avg_value=float(row["avg_value"]) if row["avg_value"] is not None else None,
                unit=row["unit"],
            )
            for row in rows
        ]

    def list_feature_text_values(
        self,
        feature_name: str,
        extractor: str | None = None,
        limit: int = 100,
    ) -> list[tuple[str, int]]:
        sql = """
            SELECT text_value, COUNT(DISTINCT track_id) AS track_count
            FROM track_features
            WHERE feature_name = ?
              AND text_value IS NOT NULL
              AND text_value != ''
        """
        params: list[object] = [feature_name]
        if extractor is not None:
            sql += " AND extractor = ?"
            params.append(extractor)
        sql += " GROUP BY text_value ORDER BY track_count DESC, text_value LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [(str(row["text_value"]), int(row["track_count"])) for row in rows]

    def search_tracks_by_features(
        self,
        filters: list[FeatureFilter],
        *,
        query: str = "",
        extractor: str | None = None,
        sort_by: str | None = None,
        sort_direction: str = "asc",
        limit: int = 50,
    ) -> list[FeatureTrack]:
        where = ["t.missing_at IS NULL"]
        params: list[object] = []
        cleaned_query = query.strip()
        if cleaned_query:
            like = f"%{cleaned_query}%"
            where.append(
                "(t.artist LIKE ? OR t.title LIKE ? OR t.album LIKE ? OR t.path LIKE ?)"
            )
            params.extend([like, like, like, like])

        for index, item in enumerate(filters):
            alias = f"f{index}"
            join_where = [
                f"{alias}.track_id = t.id",
                f"{alias}.feature_name = ?",
            ]
            params.append(item.name)
            if extractor is not None:
                join_where.append(f"{alias}.extractor = ?")
                params.append(extractor)
            if item.min_value is not None:
                join_where.append(f"{alias}.value >= ?")
                params.append(item.min_value)
            if item.max_value is not None:
                join_where.append(f"{alias}.value <= ?")
                params.append(item.max_value)
            if item.text_values:
                placeholders = ",".join("?" for _value in item.text_values)
                join_where.append(f"{alias}.text_value IN ({placeholders})")
                params.extend(item.text_values)
            where.append(f"EXISTS (SELECT 1 FROM track_features {alias} WHERE {' AND '.join(join_where)})")

        feature_where = ""
        feature_params: list[object] = []
        if extractor is not None:
            feature_where = " AND tf.extractor = ?"
            feature_params.append(extractor)

        sort_direction = "DESC" if sort_direction.lower() == "desc" else "ASC"
        inner_order_sql = "t.artist, t.album, t.title, t.id"
        outer_order_sql = "t.artist, t.album, t.title, t.id"
        sort_select = "NULL AS sort_value"
        if sort_by:
            sort_select = "sf.value AS sort_value"
            inner_order_sql = (
                f"sf.value IS NULL, sf.value {sort_direction}, t.artist, t.title, t.id"
            )
            outer_order_sql = (
                f"t.sort_value IS NULL, t.sort_value {sort_direction}, t.artist, t.title, t.id"
            )

        sort_join = ""
        sort_params: list[object] = []
        if sort_by:
            sort_join = "LEFT JOIN track_features sf ON sf.track_id = t.id AND sf.feature_name = ?"
            sort_params.append(sort_by)
            if extractor is not None:
                sort_join += " AND sf.extractor = ?"
                sort_params.append(extractor)

        sql = f"""
            SELECT t.*, tf.feature_name, tf.value, tf.text_value, tf.unit, tf.confidence, tf.extractor
            FROM (
                SELECT t.*, {sort_select}
                FROM tracks t
                {sort_join}
                WHERE {' AND '.join(where)}
                ORDER BY {inner_order_sql}
                LIMIT ?
            ) t
            LEFT JOIN track_features tf ON tf.track_id = t.id{feature_where}
            ORDER BY {outer_order_sql}, tf.extractor, tf.feature_name
        """
        all_params = [*sort_params, *params, limit, *feature_params]
        with self.connect() as conn:
            rows = conn.execute(sql, all_params).fetchall()

        grouped: dict[int, FeatureTrack] = {}
        for row in rows:
            track = grouped.get(int(row["id"]))
            if track is None:
                track = FeatureTrack(row_to_track(row), [])
                grouped[int(row["id"])] = track
            if row["feature_name"] is not None:
                track.features.append(
                    TrackFeature(
                        name=str(row["feature_name"]),
                        value=float(row["value"]) if row["value"] is not None else None,
                        text_value=row["text_value"],
                        unit=row["unit"],
                        confidence=float(row["confidence"]) if row["confidence"] is not None else None,
                        extractor=str(row["extractor"]),
                    )
                )
        return list(grouped.values())

    def list_head_summaries(self) -> list[HeadSummary]:
        with self.connect() as conn:
            output_rows = conn.execute(
                """
                SELECT
                    model_name,
                    COUNT(*) AS output_count
                FROM track_model_outputs o
                GROUP BY o.model_name
                ORDER BY o.model_name
                """
            ).fetchall()
        return [
            HeadSummary(
                model_name=str(row["model_name"]),
                output_count=int(row["output_count"] or 0),
                prediction_track_count=int(row["output_count"] or 0),
                label_count=0,
                max_score=1.0,
                avg_score=None,
            )
            for row in output_rows
        ]

    def list_head_prediction_labels(
        self,
        model_name: str,
        limit: int = 100,
    ) -> list[tuple[str, int, float | None, float | None]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    label,
                    COUNT(DISTINCT track_id) AS track_count,
                    AVG(score) AS avg_score,
                    MAX(score) AS max_score
                FROM track_predictions
                WHERE model_name = ?
                GROUP BY label
                ORDER BY track_count DESC, avg_score DESC, label
                LIMIT ?
                """,
                (model_name, limit),
            ).fetchall()
        return [
            (
                str(row["label"]),
                int(row["track_count"] or 0),
                float(row["avg_score"]) if row["avg_score"] is not None else None,
                float(row["max_score"]) if row["max_score"] is not None else None,
            )
            for row in rows
        ]

    def search_tracks_by_head_predictions(
        self,
        filters: list[FeatureFilter],
        *,
        query: str = "",
        sort_by: str | None = None,
        sort_direction: str = "desc",
        limit: int = 50,
    ) -> list[FeatureTrack]:
        where = ["t.missing_at IS NULL"]
        params: list[object] = []
        cleaned_query = query.strip()
        if cleaned_query:
            like = f"%{cleaned_query}%"
            where.append(
                "(t.artist LIKE ? OR t.title LIKE ? OR t.album LIKE ? OR t.path LIKE ?)"
            )
            params.extend([like, like, like, like])

        selected_models: set[str] = set()
        selected_labels: set[str] = set()
        for index, item in enumerate(filters):
            selected_models.add(item.name)
            alias = f"p{index}"
            prediction_where = [
                f"{alias}.track_id = t.id",
                f"{alias}.model_name = ?",
            ]
            params.append(item.name)
            if item.min_value is not None:
                prediction_where.append(f"{alias}.score >= ?")
                params.append(item.min_value)
            if item.max_value is not None:
                prediction_where.append(f"{alias}.score <= ?")
                params.append(item.max_value)
            if item.text_values:
                selected_labels.update(item.text_values)
                placeholders = ",".join("?" for _value in item.text_values)
                prediction_where.append(f"{alias}.label IN ({placeholders})")
                params.extend(item.text_values)
            where.append(
                f"EXISTS (SELECT 1 FROM track_predictions {alias} WHERE {' AND '.join(prediction_where)})"
            )
        if sort_by:
            selected_models.add(sort_by)

        sort_direction = "ASC" if sort_direction.lower() == "asc" else "DESC"
        sort_select = "NULL AS sort_value"
        sort_join = ""
        sort_params: list[object] = []
        inner_order_sql = "t.artist, t.album, t.title, t.id"
        outer_order_sql = "t.artist, t.album, t.title, t.id"
        if sort_by:
            sort_select = "sp.score AS sort_value"
            sort_join = """
                LEFT JOIN track_predictions sp
                  ON sp.track_id = t.id
                 AND sp.model_name = ?
                 AND sp.rank = 1
            """
            sort_params.append(sort_by)
            inner_order_sql = (
                f"sp.score IS NULL, sp.score {sort_direction}, t.artist, t.title, t.id"
            )
            outer_order_sql = (
                f"t.sort_value IS NULL, t.sort_value {sort_direction}, t.artist, t.title, t.id"
            )

        prediction_join = ""
        prediction_rank_join = "AND p.rank <= 3"
        prediction_params: list[object] = []
        if selected_models:
            placeholders = ",".join("?" for _model in selected_models)
            prediction_join = f" AND p.model_name IN ({placeholders})"
            prediction_params.extend(sorted(selected_models))
            if selected_labels:
                label_placeholders = ",".join("?" for _label in selected_labels)
                prediction_rank_join = f"AND (p.rank <= 3 OR p.label IN ({label_placeholders}))"
                prediction_params.extend(sorted(selected_labels))
        else:
            prediction_join = " AND 0"

        sql = f"""
            SELECT t.*, p.model_name, p.label, p.score, p.rank
            FROM (
                SELECT t.*, {sort_select}
                FROM tracks t
                {sort_join}
                WHERE {' AND '.join(where)}
                ORDER BY {inner_order_sql}
                LIMIT ?
            ) t
            LEFT JOIN track_predictions p
              ON p.track_id = t.id
             {prediction_join}
             {prediction_rank_join}
            ORDER BY {outer_order_sql}, p.model_name, p.rank
        """
        all_params = [*sort_params, *params, limit, *prediction_params]
        with self.connect() as conn:
            rows = conn.execute(sql, all_params).fetchall()

        grouped: dict[int, FeatureTrack] = {}
        for row in rows:
            track = grouped.get(int(row["id"]))
            if track is None:
                track = FeatureTrack(row_to_track(row), [])
                grouped[int(row["id"])] = track
            if row["model_name"] is not None:
                track.features.append(
                    TrackFeature(
                        name=str(row["model_name"]),
                        value=float(row["score"]) if row["score"] is not None else None,
                        text_value=row["label"],
                        unit="score",
                        extractor="discogs_effnet_heads",
                    )
                )
        return list(grouped.values())

    def count_missing_embeddings(self, model_name: str) -> int:
        with self.connect() as conn:
            return int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM tracks t
                    LEFT JOIN embeddings e
                      ON e.track_id = t.id AND e.model_name = ?
                    WHERE e.track_id IS NULL
                      AND t.missing_at IS NULL
                    """,
                    (model_name,),
                ).fetchone()[0]
            )

    def recent_tracks(self, limit: int = 50) -> list[Track]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tracks ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [row_to_track(row) for row in rows]


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
