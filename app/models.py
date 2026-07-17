"""Domain model dataclasses and constants for the discocs application.

Extracted from store.py so that models can be imported independently of
the full Store (which requires sqlite3, numpy, etc.).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
try:
    from datetime import UTC
except ImportError:
    from datetime import timezone as _tz; UTC = _tz.utc  # type: ignore[assignment]

import numpy as np


# ---------------------------------------------------------------------------
# Entity models
# ---------------------------------------------------------------------------

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
class ReleaseAggregate:
    release_id: int
    track_count: int
    available_track_count: int
    duration: float | None
    centroid_model: str | None
    medoid_track_id: int | None
    # 'pending' | 'ready' | 'unavailable'
    embedding_status: str
    top_region_matches_json: str | None
    audio_summary_json: str | None
    preference_summary_json: str | None
    updated_at: str


@dataclass(frozen=True)
class ArtistAggregate:
    artist_id: int
    release_count: int
    available_release_count: int
    centroid_model: str | None
    medoid_release_id: int | None
    # 'pending' | 'ready' | 'unavailable'
    embedding_status: str
    updated_at: str


# ---------------------------------------------------------------------------
# Flow engine
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FlowProfile:
    id: str
    # 'pending' | 'building' | 'ready' | 'empty' | 'stale'
    status: str
    model_key: str
    settings_json: str | None
    created_at: str
    updated_at: str
    last_built_at: str | None


@dataclass(frozen=True)
class FlowRegion:
    id: str
    profile_id: str
    region_index: int
    centroid_ref: str | None
    medoid_track_id: int | None
    weight: float
    seed_count: int
    candidate_count: int
    summary_json: str | None
    quality_json: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class FlowRegionTrack:
    region_id: str
    track_id: int
    # 'seed' | 'representative' | 'candidate' | 'accepted' | 'rejected'
    role: str
    weight: float | None
    distance: float | None


@dataclass(frozen=True)
class FlowGenerationRun:
    id: str
    session_id: str | None
    profile_id: str | None
    region_id: str | None
    settings_json: str | None
    candidate_count: int | None
    selected_count: int | None
    score_summary_json: str | None
    created_at: str


@dataclass(frozen=True)
class MapProjection:
    """A persisted 2D projection of one embedding model (collection map).

    Diagnostic snapshot only — real similarity keeps using HNSW/cosine.
    """
    id: str
    model_name: str
    name: str
    # 'umap' | 'pca' (later: 'tsne')
    method: str
    # embedding-space distance metric used for the projection
    metric: str
    params_json: str | None
    source_embedding_count: int
    projected_count: int
    skipped_count: int
    embedding_dim: int
    version: int
    # 'pending' | 'running' | 'ready' | 'failed'
    status: str
    diagnostics_json: str | None
    created_at: str
    completed_at: str | None


# ---------------------------------------------------------------------------
# Row / projection types
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Instant mix
# ---------------------------------------------------------------------------

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
class InstantMixRequestParams:
    effective_model: str
    effective_count: int
    max_per_artist: int
    exclude_same_album: bool
    count_collaboration_artists: bool
    requested_model: str | None = None
    requested_count: int | None = None
    requested_max_per_artist: int | None = None
    requested_exclude_same_album: bool | None = None
    min_similarity: float | None = None


@dataclass(frozen=True)
class InstantMixRequestRecord:
    request_id: str
    seed_item_id: str
    model_name: str
    params: InstantMixRequestParams
    status: str
    provider: str = "navidrome"
    seed_track_id: int | None = None
    results: tuple[object, ...] = ()
    skipped_without_external_id: int = 0
    duration_ms: float | None = None
    error: str | None = None
    created_at: str | None = None


# ---------------------------------------------------------------------------
# Track listings / similarity
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TrackListing:
    track: Track
    has_embedding: bool
    predictions: list[TrackPrediction] | None = None


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


# ---------------------------------------------------------------------------
# Audio features
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Analysis jobs / tasks / workers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Playback
# ---------------------------------------------------------------------------

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
class PlaybackEventCreate:
    event_type: str
    session_id: str | None = None
    queue_item_id: str | None = None
    track_id: int | None = None
    release_id: int | None = None
    artist_id: int | None = None
    position_seconds: float | None = None
    duration_seconds: float | None = None
    play_fraction: float | None = None
    client_event_id: str | None = None
    source: str = "web"
    payload: dict[str, object] | None = None
    event_id: str | None = None
    created_at: str | None = None


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


# ---------------------------------------------------------------------------
# Generated mixes / playlists
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GeneratedMix:
    id: str
    title: str
    mix_type: str
    status: str
    cover_path: str | None
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


@dataclass(frozen=True)
class Playlist:
    id: int
    user_id: int
    title: str
    kind: str
    description: str | None
    cover_path: str | None
    source_json: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PlaylistItem:
    playlist_id: int
    position: int
    track_id: int
    created_at: str


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def utc_now() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Domain constants
# ---------------------------------------------------------------------------

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
PLAYLIST_KINDS = {"manual", "saved_mix"}
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
