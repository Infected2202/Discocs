"""Store Core Store: __init__, connect, init, schema.

Part of the app/store package — Stage 7 refactoring.
Do not import this module directly; use app.store instead.
"""
from __future__ import annotations

from __future__ import annotations

import logging
import json
import os
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
_INT_NOT_NULL_DEFAULT_0 = "INTEGER NOT NULL DEFAULT 0"

# Phase 2 multiuser (plans/multiuser-spec.md §2). Personal tables that gain a
# nullable ``user_id`` column and whose pre-existing rows are back-filled to the
# owner user on first upgrade. PK-change tables (user_*_preferences,
# albums_for_you_cache) migrate later, together with their store scoping, so a
# schema change never lands ahead of the code that reads it.
OWNER_BACKFILL_TABLES: tuple[str, ...] = (
    "playback_sessions",
    "playback_events",
    "flow_profiles",
    "generated_mixes",
    "playlists",
)
# Env naming the owner (a Navidrome username) who inherits all pre-existing
# personal data. Required only when unscoped rows actually exist — see
# _migrate_owner_backfill.
OWNER_USER_ENV = "DISCOCS_OWNER_USER"
# Fallback owner username for dev/test/empty installs where the env is unset.
# Production is protected by _migrate_owner_backfill (raises on upgrade with
# data if the env is missing), so this only ever names the single implicit user
# in tests and fresh dev databases.
DEFAULT_OWNER_USERNAME = "owner"
_DEFAULT_USER = object()

# Preference tables whose PK moves from entity-only to (user_id, entity_id).
# Maps table -> entity-id column. Rebuilt in place by _migrate_preference_pks.
PREFERENCE_PK_TABLES: dict[str, str] = {
    "user_track_preferences": "track_id",
    "user_release_preferences": "release_id",
    "user_artist_preferences": "artist_id",
}
_PREFERENCE_TABLE_DDL: dict[str, str] = {
    "user_track_preferences": """CREATE TABLE user_track_preferences (
        user_id INTEGER NOT NULL, track_id INTEGER NOT NULL,
        liked INTEGER NOT NULL DEFAULT 0, disliked INTEGER NOT NULL DEFAULT 0,
        play_count INTEGER NOT NULL DEFAULT 0, completion_count INTEGER NOT NULL DEFAULT 0,
        skip_count INTEGER NOT NULL DEFAULT 0, early_skip_count INTEGER NOT NULL DEFAULT 0,
        replay_count INTEGER NOT NULL DEFAULT 0, last_played_at TEXT,
        last_completed_at TEXT, last_skipped_at TEXT, score REAL NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL, PRIMARY KEY (user_id, track_id),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)""",
    "user_release_preferences": """CREATE TABLE user_release_preferences (
        user_id INTEGER NOT NULL, release_id INTEGER NOT NULL,
        liked INTEGER NOT NULL DEFAULT 0, play_count INTEGER NOT NULL DEFAULT 0,
        completion_count INTEGER NOT NULL DEFAULT 0, skip_count INTEGER NOT NULL DEFAULT 0,
        last_played_at TEXT, last_completed_at TEXT, score REAL NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL, PRIMARY KEY (user_id, release_id),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)""",
    "user_artist_preferences": """CREATE TABLE user_artist_preferences (
        user_id INTEGER NOT NULL, artist_id INTEGER NOT NULL,
        liked INTEGER NOT NULL DEFAULT 0, play_count INTEGER NOT NULL DEFAULT 0,
        completion_count INTEGER NOT NULL DEFAULT 0, skip_count INTEGER NOT NULL DEFAULT 0,
        last_played_at TEXT, score REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, artist_id),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)""",
}

class StoreBase:
    def __init__(self, db_path: Path, *, user_id: int | None | object = _DEFAULT_USER):
        self.db_path = db_path
        self.user_id = None if user_id is _DEFAULT_USER else user_id
        self._bind_default_user = user_id is _DEFAULT_USER
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def for_user(self, user_id: int) -> "StoreBase":
        """Return the same store type bound to one mandatory user identity."""
        if user_id <= 0:
            raise ValueError("user_id must be a positive integer")
        return type(self)(self.db_path, user_id=user_id)

    def require_user_id(self) -> int:
        if self.user_id is None:
            raise PermissionError("A user-scoped store is required for personal data")
        return self.user_id

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level="IMMEDIATE")
        conn.row_factory = sqlite3.Row
        conn.create_function("discocs_user_id", 0, self.require_user_id)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA wal_autocheckpoint = 1000")
        return conn

    def init(self) -> None:
        resolved_path = self.db_path.resolve()
        with INIT_LOCK:
            if resolved_path not in INITIALIZED_DB_PATHS:
                self._init_schema()
                INITIALIZED_DB_PATHS.add(resolved_path)
        if self._bind_default_user and self.user_id is None:
            owner_username = os.getenv(OWNER_USER_ENV, "").strip() or DEFAULT_OWNER_USERNAME
            owner = self.get_user_by_username(owner_username)
            self.user_id = (
                int(owner["id"])
                if owner is not None
                else self.upsert_user(owner_username, now=utc_now())
            )

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

                -- Per-user preferences (Phase 2). PK is (user_id, entity_id):
                -- one row per user per track/release/artist. Fresh installs get
                -- this shape directly; existing single-user DBs are rebuilt from
                -- the old entity-only PK by _migrate_preference_pks (rows bound
                -- to the owner). Keep these column lists in sync with that
                -- migration's rebuild DDL.
                CREATE TABLE IF NOT EXISTS user_track_preferences (
                    user_id INTEGER NOT NULL,
                    track_id INTEGER NOT NULL,
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
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, track_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_release_preferences (
                    user_id INTEGER NOT NULL,
                    release_id INTEGER NOT NULL,
                    liked INTEGER NOT NULL DEFAULT 0,
                    play_count INTEGER NOT NULL DEFAULT 0,
                    completion_count INTEGER NOT NULL DEFAULT 0,
                    skip_count INTEGER NOT NULL DEFAULT 0,
                    last_played_at TEXT,
                    last_completed_at TEXT,
                    score REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, release_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_artist_preferences (
                    user_id INTEGER NOT NULL,
                    artist_id INTEGER NOT NULL,
                    liked INTEGER NOT NULL DEFAULT 0,
                    play_count INTEGER NOT NULL DEFAULT 0,
                    completion_count INTEGER NOT NULL DEFAULT 0,
                    skip_count INTEGER NOT NULL DEFAULT 0,
                    last_played_at TEXT,
                    score REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, artist_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS generated_mixes (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    mix_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    cover_path TEXT,
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

                CREATE TABLE IF NOT EXISTS playlists (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    source_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS playlist_items (
                    playlist_id INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    track_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (playlist_id, position),
                    UNIQUE (playlist_id, track_id),
                    FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
                    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_playlist_items_track
                    ON playlist_items(track_id);

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

                CREATE TABLE IF NOT EXISTS release_aggregates (
                    release_id INTEGER PRIMARY KEY,
                    track_count INTEGER NOT NULL DEFAULT 0,
                    available_track_count INTEGER NOT NULL DEFAULT 0,
                    duration REAL,
                    centroid_model TEXT,
                    medoid_track_id INTEGER,
                    embedding_status TEXT NOT NULL DEFAULT 'pending',
                    top_region_matches_json TEXT,
                    audio_summary_json TEXT,
                    preference_summary_json TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (release_id) REFERENCES releases(id) ON DELETE CASCADE,
                    FOREIGN KEY (medoid_track_id) REFERENCES tracks(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS release_embeddings (
                    release_id INTEGER NOT NULL,
                    model_name TEXT NOT NULL,
                    dim INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    vector_norm REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (release_id, model_name),
                    FOREIGN KEY (release_id) REFERENCES releases(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS flow_profiles (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'pending',
                    model_key TEXT NOT NULL,
                    settings_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_built_at TEXT
                );

                CREATE TABLE IF NOT EXISTS flow_regions (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    region_index INTEGER NOT NULL,
                    centroid_ref TEXT,
                    medoid_track_id INTEGER,
                    weight REAL NOT NULL DEFAULT 1.0,
                    seed_count INTEGER NOT NULL DEFAULT 0,
                    candidate_count INTEGER NOT NULL DEFAULT 0,
                    summary_json TEXT,
                    quality_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (profile_id) REFERENCES flow_profiles(id) ON DELETE CASCADE,
                    FOREIGN KEY (medoid_track_id) REFERENCES tracks(id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_flow_regions_profile
                    ON flow_regions(profile_id, region_index);

                CREATE TABLE IF NOT EXISTS flow_region_tracks (
                    region_id TEXT NOT NULL,
                    track_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    weight REAL,
                    distance REAL,
                    PRIMARY KEY (region_id, track_id, role),
                    FOREIGN KEY (region_id) REFERENCES flow_regions(id) ON DELETE CASCADE,
                    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_flow_region_tracks_region_role
                    ON flow_region_tracks(region_id, role);

                CREATE TABLE IF NOT EXISTS flow_region_embeddings (
                    region_id TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    dim INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    vector_norm REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (region_id, model_name),
                    FOREIGN KEY (region_id) REFERENCES flow_regions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS flow_generation_runs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    profile_id TEXT,
                    region_id TEXT,
                    settings_json TEXT,
                    candidate_count INTEGER,
                    selected_count INTEGER,
                    score_summary_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (profile_id) REFERENCES flow_profiles(id) ON DELETE SET NULL,
                    FOREIGN KEY (region_id) REFERENCES flow_regions(id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_flow_generation_runs_session
                    ON flow_generation_runs(session_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS albums_for_you_cache (
                    user_id INTEGER NOT NULL,
                    model_name TEXT NOT NULL,
                    items_json TEXT NOT NULL,
                    computed_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, model_name),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

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

                -- Auth sessions. Only the SHA-256 of the opaque session token is
                -- stored, never the token itself: a DB leak cannot resurrect a
                -- live session. No password is stored (Navidrome verifies creds
                -- at login; the password is discarded afterwards).
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    ip TEXT,
                    user_agent TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_expires
                    ON sessions(expires_at);

                -- Application users (Phase 2 multiuser). Identity is the
                -- Navidrome login; a row is auto-created on first successful
                -- login (upsert by navidrome_username). The internal integer
                -- ``id`` is the FK used to scope every personal table, so it
                -- survives a rename in Navidrome. No role/admin flag — global
                -- operations live behind the localhost admin, see docs/auth.md.
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    navidrome_username TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT
                );

                -- Collection map / embedding atlas. A projection is a persisted
                -- snapshot of 2D coordinates for one embedding model; multiple
                -- projections per model are allowed. This is a diagnostic view
                -- only — real similarity/neighbors keep using HNSW/cosine.
                CREATE TABLE IF NOT EXISTS map_projections (
                    id TEXT PRIMARY KEY,
                    model_name TEXT NOT NULL,
                    name TEXT NOT NULL,
                    method TEXT NOT NULL,
                    metric TEXT NOT NULL DEFAULT 'cosine',
                    params_json TEXT,
                    source_embedding_count INTEGER NOT NULL DEFAULT 0,
                    projected_count INTEGER NOT NULL DEFAULT 0,
                    skipped_count INTEGER NOT NULL DEFAULT 0,
                    embedding_dim INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'pending',
                    diagnostics_json TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_map_projections_model
                    ON map_projections(model_name, created_at DESC);

                CREATE TABLE IF NOT EXISTS map_projection_points (
                    projection_id TEXT NOT NULL,
                    track_id INTEGER NOT NULL,
                    x REAL NOT NULL,
                    y REAL NOT NULL,
                    PRIMARY KEY (projection_id, track_id),
                    FOREIGN KEY (projection_id) REFERENCES map_projections(id) ON DELETE CASCADE,
                    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
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
            self._ensure_column(conn, "analysis_jobs", "progress_done", _INT_NOT_NULL_DEFAULT_0)
            self._ensure_column(conn, "analysis_jobs", "progress_failed", _INT_NOT_NULL_DEFAULT_0)
            self._ensure_column(conn, "analysis_workers", "claimed_count", _INT_NOT_NULL_DEFAULT_0)
            self._ensure_column(conn, "analysis_workers", "completed_count", _INT_NOT_NULL_DEFAULT_0)
            self._ensure_column(conn, "analysis_workers", "failed_count", _INT_NOT_NULL_DEFAULT_0)
            self._ensure_column(conn, "analysis_workers", "released_count", _INT_NOT_NULL_DEFAULT_0)
            self._ensure_column(conn, "analysis_workers", "current_task_id", "TEXT")
            self._ensure_column(conn, "analysis_workers", "stage", "TEXT")
            self._ensure_column(conn, "analysis_workers", "message", "TEXT")
            self._ensure_column(conn, "playback_sessions", "source_label", "TEXT")
            self._ensure_column(conn, "playback_sessions", "current_queue_item_id", "TEXT")
            self._ensure_column(conn, "playback_sessions", "settings_json", "TEXT")
            self._ensure_column(conn, "playback_sessions", "state_json", "TEXT")
            self._ensure_column(conn, "queue_items", "locked", _INT_NOT_NULL_DEFAULT_0)
            self._ensure_column(conn, "queue_items", "reason", "TEXT")
            self._ensure_column(conn, "queue_items", "score", "REAL")
            self._ensure_column(conn, "queue_items", "debug_json", "TEXT")
            self._ensure_column(conn, "playback_events", "release_id", "INTEGER")
            self._ensure_column(conn, "playback_events", "artist_id", "INTEGER")
            self._ensure_column(conn, "playback_events", "source", "TEXT NOT NULL DEFAULT 'web'")
            self._ensure_column(conn, "playback_events", "payload_json", "TEXT")
            self._ensure_column(conn, "generated_mixes", "cover_path", "TEXT")
            self._ensure_column(conn, "playlists", "description", "TEXT")
            self._ensure_column(conn, "playlists", "cover_path", "TEXT")
            # Phase 2: link a session to its user. Legacy (pre-users) sessions
            # keep user_id NULL and resolve it lazily via the users table.
            self._ensure_column(conn, "sessions", "user_id", "INTEGER")
            # Phase 2: personal ownership on the additive tables (nullable so
            # existing INSERTs keep working; back-filled to the owner below).
            self._ensure_column(conn, "playback_sessions", "user_id", "INTEGER")
            self._ensure_column(conn, "playback_events", "user_id", "INTEGER")
            self._ensure_column(conn, "flow_profiles", "user_id", "INTEGER")
            self._ensure_column(conn, "generated_mixes", "user_id", "INTEGER")
            self._ensure_column(conn, "playlists", "user_id", "INTEGER")
            conn.execute("DROP INDEX IF EXISTS idx_playback_events_client_event_id")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_playback_events_user_client_event "
                "ON playback_events(user_id, client_event_id) "
                "WHERE client_event_id IS NOT NULL"
            )
            conn.execute("DROP INDEX IF EXISTS idx_flow_profiles_model_key")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_flow_profiles_user_model "
                "ON flow_profiles(user_id, model_key)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_playback_sessions_user "
                "ON playback_sessions(user_id, updated_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_playback_events_user_created "
                "ON playback_events(user_id, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_flow_profiles_user "
                "ON flow_profiles(user_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_generated_mixes_user_updated "
                "ON generated_mixes(user_id, updated_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_playlists_user "
                "ON playlists(user_id)"
            )
            self._backfill_added_timestamps(conn)
        self._migrate_owner_backfill()
        self._migrate_preference_pks()
        self._migrate_albums_cache_pk()

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

    # ------------------------------------------------------------------
    # Phase 2 multiuser: owner back-fill (plans/multiuser-spec.md §2)
    # ------------------------------------------------------------------
    def _count_unscoped_personal_rows(self, conn: sqlite3.Connection) -> int:
        """Count rows across OWNER_BACKFILL_TABLES that still have user_id NULL.

        Zero means either a fresh/empty DB or an already-migrated one — in both
        cases the back-fill is a no-op and the owner env is not required.
        """
        total = 0
        for table in OWNER_BACKFILL_TABLES:
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE user_id IS NULL"
            ).fetchone()
            total += int(row["n"])
        return total

    def _upsert_owner_user_id(
        self, conn: sqlite3.Connection, navidrome_username: str, now: str
    ) -> int:
        """Ensure a users row for the owner exists; return its id.

        Migration is not a login, so ``last_login_at`` is left NULL for a
        freshly created owner and an existing owner row is not touched.
        """
        conn.execute(
            """
            INSERT OR IGNORE INTO users (navidrome_username, created_at, last_login_at)
            VALUES (?, ?, NULL)
            """,
            (navidrome_username, now),
        )
        row = conn.execute(
            "SELECT id FROM users WHERE navidrome_username = ?",
            (navidrome_username,),
        ).fetchone()
        return int(row["id"])

    def _backup_db(self, tag: str) -> Path | None:
        """Snapshot the live DB before a data migration (spec §2 safeguard #1).

        Uses ``VACUUM INTO`` for a consistent single-file copy (WAL-safe). No-op
        for in-memory or not-yet-created databases (tests / fresh installs).
        """
        if str(self.db_path) == ":memory:" or not self.db_path.exists():
            return None
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        dest = self.db_path.with_name(f"{self.db_path.name}.{tag}-{ts}.bak")
        backup_conn = sqlite3.connect(self.db_path, isolation_level=None)
        try:
            backup_conn.execute("VACUUM INTO ?", (str(dest),))
        finally:
            backup_conn.close()
        logger.info("Backed up %s -> %s before %s migration", self.db_path, dest, tag)
        return dest

    def _migrate_owner_backfill(self) -> None:
        """Assign every pre-existing personal row to the owner user.

        Idempotent: only rows with user_id NULL are touched, so re-running (a
        second process, a restart) is a no-op. If unscoped rows exist but
        ``DISCOCS_OWNER_USER`` is unset/unresolvable the migration raises rather
        than silently orphaning history to NULL (spec §2 owner contract).
        """
        with self.connect() as conn:
            pending = self._count_unscoped_personal_rows(conn)
        if pending == 0:
            return

        owner = os.getenv(OWNER_USER_ENV, "").strip()
        if not owner:
            raise RuntimeError(
                f"{OWNER_USER_ENV} is required to migrate {pending} pre-existing "
                "personal row(s) to an owner; refusing to leave user_id NULL. "
                "Set it to the owner's Navidrome username and restart."
            )

        self._backup_db("premigrate-owner")
        now = utc_now()
        # Single transaction: partial ownership is never observable.
        with self.connect() as conn:
            owner_id = self._upsert_owner_user_id(conn, owner, now)
            for table in OWNER_BACKFILL_TABLES:
                conn.execute(
                    f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL",
                    (owner_id,),
                )
            remaining = self._count_unscoped_personal_rows(conn)
            if remaining != 0:  # pragma: no cover - defensive
                raise RuntimeError(
                    f"owner back-fill left {remaining} personal row(s) unscoped"
                )
        logger.info(
            "Owner back-fill: assigned %d personal row(s) to user_id=%d (%s)",
            pending,
            owner_id,
            owner,
        )

    def _column_exists(self, conn: sqlite3.Connection, table: str, column: str) -> bool:
        return any(
            row["name"] == column
            for row in conn.execute(f"PRAGMA table_info({table})")
        )

    def _migrate_preference_pks(self) -> None:
        """Rebuild preference tables from entity-only PK to (user_id, entity_id).

        SQLite cannot alter a PRIMARY KEY in place, so each old-schema table is
        recreated and its rows copied, bound to the owner user. Detected by the
        absence of the ``user_id`` column, so fresh installs (already built with
        the new shape) and re-runs are no-ops. Same owner contract and
        safeguards as _migrate_owner_backfill: loud failure without an owner
        when rows exist, backup first, one transaction.
        """
        with self.connect() as conn:
            pending = [
                t for t in PREFERENCE_PK_TABLES if not self._column_exists(conn, t, "user_id")
            ]
            has_rows = any(
                conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone() is not None
                for t in pending
            )
        if not pending:
            return

        owner = os.getenv(OWNER_USER_ENV, "").strip()
        if has_rows and not owner:
            raise RuntimeError(
                f"{OWNER_USER_ENV} is required to migrate existing preference rows "
                "to an owner PK; refusing to drop or orphan them. Set it to the "
                "owner's Navidrome username and restart."
            )

        self._backup_db("premigrate-prefpk")
        now = utc_now()
        with self.connect() as conn:
            owner_id = self._upsert_owner_user_id(conn, owner, now) if has_rows else None
            for table in pending:
                self._rebuild_preference_table(
                    conn, table, owner_id
                )
        logger.info("Preference PK migration: rebuilt %s", ", ".join(pending))

    def _rebuild_preference_table(
        self,
        conn: sqlite3.Connection,
        table: str,
        owner_id: int | None,
    ) -> None:
        """Copy an old entity-PK preference table into the new composite-PK one.

        Column list is read from the old table so the copy stays correct even if
        columns drift; ``user_id`` is prepended with the owner id for every row.
        """
        old_cols = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
        col_list = ", ".join(old_cols)
        conn.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
        conn.execute(_PREFERENCE_TABLE_DDL[table])
        if owner_id is not None:
            conn.execute(
                f"INSERT INTO {table} (user_id, {col_list}) "
                f"SELECT ?, {col_list} FROM {table}_old",
                (owner_id,),
            )
        conn.execute(f"DROP TABLE {table}_old")

    def _migrate_albums_cache_pk(self) -> None:
        """Move the personalized albums cache to a per-user composite key."""
        with self.connect() as conn:
            if self._column_exists(conn, "albums_for_you_cache", "user_id"):
                return
            has_rows = conn.execute(
                "SELECT 1 FROM albums_for_you_cache LIMIT 1"
            ).fetchone() is not None
        owner = os.getenv(OWNER_USER_ENV, "").strip()
        if has_rows and not owner:
            raise RuntimeError(
                f"{OWNER_USER_ENV} is required to migrate albums_for_you_cache"
            )
        self._backup_db("premigrate-albums-cache")
        with self.connect() as conn:
            owner_id = self._upsert_owner_user_id(conn, owner, utc_now()) if has_rows else None
            conn.execute("ALTER TABLE albums_for_you_cache RENAME TO albums_for_you_cache_old")
            conn.execute(
                """CREATE TABLE albums_for_you_cache (
                    user_id INTEGER NOT NULL,
                    model_name TEXT NOT NULL,
                    items_json TEXT NOT NULL,
                    computed_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, model_name),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )"""
            )
            if owner_id is not None:
                conn.execute(
                    """INSERT INTO albums_for_you_cache
                       (user_id, model_name, items_json, computed_at)
                       SELECT ?, model_name, items_json, computed_at
                       FROM albums_for_you_cache_old""",
                    (owner_id,),
                )
            conn.execute("DROP TABLE albums_for_you_cache_old")

