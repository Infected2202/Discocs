"""API request Pydantic models.

Extracted from app/main.py.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.audio_features import AUDIO_FEATURE_EXTRACTOR
from app.state import (
    DEFAULT_ANALYZE_TF_THREADS,
    DEFAULT_ANALYZE_WORKERS,
    DEFAULT_AUDIO_FEATURE_WORKERS,
    MAX_ANALYZE_TF_THREADS,
    MAX_ANALYZE_WORKERS,
    MAX_AUDIO_FEATURE_WORKERS,
)

_EXECUTION_MODE_PATTERN = "^(both|local|remote)$"


# ---------------------------------------------------------------------------
# Analysis / worker
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    model: str = "discogs_multi"
    limit: int | None = Field(default=None, ge=1)
    workers: int = Field(default=DEFAULT_ANALYZE_WORKERS, ge=1, le=MAX_ANALYZE_WORKERS)
    tf_threads: int = Field(
        default=DEFAULT_ANALYZE_TF_THREADS,
        ge=1,
        le=MAX_ANALYZE_TF_THREADS,
    )
    local_executor_enabled: bool = True
    max_attempts: int = Field(default=3, ge=1, le=20)
    execution_mode: str = Field(default="both", pattern=_EXECUTION_MODE_PATTERN)


class WorkerRegisterRequest(BaseModel):
    worker_id: str
    models: list[str] = Field(default_factory=list)


class WorkerClaimRequest(BaseModel):
    worker_id: str
    models: list[str] = Field(default_factory=list)
    limit: int = Field(default=16, ge=1, le=500)
    lease_seconds: int = Field(default=300, ge=30, le=3600)


class WorkerResultItem(BaseModel):
    task_id: str
    track_id: int
    model_name: str
    dim: int = Field(ge=1)
    dtype: str = "float32"
    vector_b64: str
    file_size: int
    mtime: int


class WorkerFeatureItem(BaseModel):
    name: str
    value: float | None = None
    text_value: str | None = None
    unit: str | None = None
    confidence: float | None = None
    extractor: str = AUDIO_FEATURE_EXTRACTOR


class WorkerFeatureResultItem(BaseModel):
    task_id: str
    track_id: int
    model_name: str = AUDIO_FEATURE_EXTRACTOR
    file_size: int
    mtime: int
    features: list[WorkerFeatureItem] = Field(default_factory=list)
    timeline_manifest: dict[str, object]
    timeline_payload_b64: str


class WorkerPredictionItem(BaseModel):
    label: str
    score: float
    rank: int


class WorkerHeadOutputItem(BaseModel):
    model_name: str
    dim: int = Field(ge=1)
    dtype: str = "float32"
    aggregation: str
    scores_b64: str
    predictions: list[WorkerPredictionItem] = Field(default_factory=list)


class WorkerHeadResultItem(BaseModel):
    task_id: str
    track_id: int
    model_name: str = "discogs-effnet-heads"
    file_size: int
    mtime: int
    outputs: list[WorkerHeadOutputItem] = Field(default_factory=list)


class WorkerSubmitRequest(BaseModel):
    worker_id: str
    results: list[WorkerResultItem] = Field(default_factory=list)
    feature_results: list[WorkerFeatureResultItem] = Field(default_factory=list)
    head_results: list[WorkerHeadResultItem] = Field(default_factory=list)


class WorkerFailureItem(BaseModel):
    task_id: str
    error: str
    error_type: str = "WorkerError"
    stage: str = "worker"
    retryable: bool = True


class WorkerFailuresRequest(BaseModel):
    worker_id: str
    failures: list[WorkerFailureItem] = Field(default_factory=list)


class WorkerReleaseRequest(BaseModel):
    worker_id: str
    task_ids: list[str] | None = None


class CancelJobRequest(BaseModel):
    reason: str = "Cancelled by user"


class AnalyzeHeadsRequest(BaseModel):
    limit: int | None = Field(default=None, ge=1)
    local_executor_enabled: bool = True
    max_attempts: int = Field(default=3, ge=1, le=20)
    execution_mode: str = Field(default="both", pattern=_EXECUTION_MODE_PATTERN)


class AnalyzeAudioFeaturesRequest(BaseModel):
    limit: int | None = Field(default=None, ge=1)
    workers: int = Field(default=DEFAULT_AUDIO_FEATURE_WORKERS, ge=1, le=MAX_AUDIO_FEATURE_WORKERS)
    local_executor_enabled: bool = True
    max_attempts: int = Field(default=3, ge=1, le=20)
    execution_mode: str = Field(default="both", pattern=_EXECUTION_MODE_PATTERN)
    reset_existing: bool = False
    extractor: str = AUDIO_FEATURE_EXTRACTOR


class TimelineStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    track_ids: list[int] = Field(min_length=1, max_length=100)
    extractor: str = "timeline_foundation_v2"


class DeleteTracksRequest(BaseModel):
    track_ids: list[int] = Field(default_factory=list)
    all_missing: bool = False


class DeleteAnalysisErrorsRequest(BaseModel):
    task_ids: list[str] = Field(default_factory=list)
    all_errors: bool = False


class IndexRequest(BaseModel):
    model: str = "discogs_multi"


class MapProjectionBuildRequest(BaseModel):
    """Enqueue a collection-map 2D projection build (runs as a backend background task)."""

    model: str = "discogs_multi"
    profile: str = "umap_local"
    force: bool = False


# ---------------------------------------------------------------------------
# Navidrome
# ---------------------------------------------------------------------------

class NavidromeSyncRequest(BaseModel):
    page_size: int = Field(default=2000, ge=1, le=2000)
    limit: int | None = Field(default=None, ge=1)
    mark_stale: bool = True


class NavidromeSettingsRequest(BaseModel):
    url: str = ""
    user: str = ""
    password: str | None = None
    auth_mode: str = Field(default="token", pattern="^(token|password)$")
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    download_mode: str = Field(default="download", pattern="^(download|stream)$")
    temp_dir: str | None = None


class NavidromeStarRequest(BaseModel):
    starred: bool


class NavidromePluginEventRequest(BaseModel):
    event: str
    item_id: str | None = None
    model: str | None = None
    count: int | None = None
    status: int | None = None
    discocs_url: str | None = None
    message: str | None = None


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class InstantMixSettingsRequest(BaseModel):
    model: str = "discogs_multi"
    count: int = Field(default=50, ge=1, le=500)
    min_similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    max_per_artist: int = Field(default=2, ge=1, le=100)
    exclude_same_album: bool = True
    count_collaboration_artists: bool = True


class UserSettingsPatchRequest(BaseModel):
    """Partial update for per-user settings; unset fields are left untouched."""

    language: str | None = Field(default=None, pattern="^(en|ru)$")
    transcoding_enabled: bool | None = None
    transcoding_bitrate_kbps: Literal[96, 128, 192, 256, 320] | None = None

    model_config = ConfigDict(extra="forbid")


class GeneratedMixSettingsRequest(BaseModel):
    mix_dashboard_count: int | None = Field(default=None, ge=1, le=20)
    mix_tracks_per_mix: int | None = Field(default=None, ge=1, le=300)
    mix_update_cadence: str | None = Field(default=None, pattern="^(manual|daily|weekly)$")
    mix_region_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    mix_discovery_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    mix_novelty_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    mix_duplicate_strictness: str | None = Field(default=None, pattern="^(strict|soft)$")
    mix_seed_source: str | None = Field(default=None, pattern="^(listening_history|track_likes_only|positive_history)$")
    mix_max_per_artist: int | None = Field(default=None, ge=1, le=50)
    mix_max_per_release: int | None = Field(default=None, ge=1, le=50)
    mix_candidate_pool: int | None = Field(default=None, ge=10, le=5000)
    mix_model: str | None = None


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

class TextSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    count: int = Field(default=50, ge=1, le=500)
    min_similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    max_per_artist: int = Field(default=100, ge=1, le=100)
    exclude_same_album: bool = False
    count_collaboration_artists: bool = True


class FeatureFilterRequest(BaseModel):
    name: str
    min_value: float | None = None
    max_value: float | None = None
    text_values: list[str] = Field(default_factory=list)


class FeatureSearchRequest(BaseModel):
    source: str = Field(default="audio_features", pattern="^(audio_features|heads)$")
    extractor: str = AUDIO_FEATURE_EXTRACTOR
    query: str = ""
    filters: list[FeatureFilterRequest] = Field(default_factory=list)
    sort_by: str | None = None
    sort_direction: str = Field(default="asc", pattern="^(asc|desc)$")
    limit: int = Field(default=50, ge=1, le=500)


class FeedbackRequest(BaseModel):
    seed_track_id: int
    result_track_id: int
    model: str = "discogs_multi"
    rating: int
    note: str | None = None


# ---------------------------------------------------------------------------
# Playback
# ---------------------------------------------------------------------------

class PlaybackSessionCreateRequest(BaseModel):
    source_type: str = Field(pattern="^(release|artist|track|playlist|search|flow|autoplay|manual|generated_mix)$")
    source_id: int | None = None
    source_label: str | None = None
    mode: str = Field(default="linear", pattern="^(linear|shuffle|radio|flow|autoplay)$")
    track_id: int | None = None
    track_ids: list[int] = Field(default_factory=list)
    autoplay_enabled: bool = True
    shuffle_enabled: bool = False
    repeat_mode: str = Field(default="off", pattern="^(off|one|all)$")
    settings: dict[str, object] = Field(default_factory=dict)
    state: dict[str, object] = Field(default_factory=dict)


class PlaybackSessionPatchRequest(BaseModel):
    status: str | None = Field(default=None, pattern="^(active|paused|ended)$")
    current_track_id: int | None = None
    current_queue_item_id: str | None = None
    autoplay_enabled: bool | None = None
    shuffle_enabled: bool | None = None
    repeat_mode: str | None = Field(default=None, pattern="^(off|one|all)$")
    settings: dict[str, object] | None = None
    state: dict[str, object] | None = None


class PlaybackQueueItemRequest(BaseModel):
    track_id: int
    origin: str = Field(default="manual", pattern="^(source|manual|autoplay|flow|generated_mix)$")
    source_type: str | None = Field(default=None, pattern="^(release|artist|track|playlist|search|flow|autoplay|manual|generated_mix)$")
    source_id: int | None = None
    locked: bool = False
    reason: str | None = None
    score: float | None = None
    debug: dict[str, object] | None = None


class PlaybackQueuePatchRequest(BaseModel):
    operation: str = Field(pattern="^(replace|add|remove|move|jump|mark_current|handover)$")
    queue_item_id: str | None = None
    client_handover_id: str | None = Field(default=None, min_length=1, max_length=128)
    track_id: int | None = None
    track_ids: list[int] = Field(default_factory=list)
    position: int | None = Field(default=None, ge=0)
    items: list[PlaybackQueueItemRequest] = Field(default_factory=list)


class PlaybackEventRequest(BaseModel):
    session_id: str | None = None
    queue_item_id: str | None = None
    track_id: int | None = None
    release_id: int | None = None
    artist_id: int | None = None
    event_type: str = Field(
        pattern=(
            "^(track_started|progress|play_threshold_reached|completed|skipped|queue_click|"
            "incoming_started|handover_completed|manual_transition_completed|manual_transition_cancelled|"
            "liked|unliked|disliked|replayed|removed_from_queue|saved_to_playlist|"
            "autoplay_toggled|preference_changed)$"
        )
    )
    position_seconds: float | None = Field(default=None, ge=0.0)
    duration_seconds: float | None = Field(default=None, ge=0.0)
    play_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    client_event_id: str | None = None
    source: str = "web"
    payload: dict[str, object] = Field(default_factory=dict)


class AutoplayRefillRequest(BaseModel):
    session_id: str
    visible_buffer: int | None = Field(default=None, ge=1, le=50)
    candidate_count: int | None = Field(default=None, ge=1, le=500)
    settings: dict[str, object] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Mixes
# ---------------------------------------------------------------------------

class MixGenerateRequest(BaseModel):
    count: int = Field(default=6, ge=1, le=20)
    tracks_per_mix: int = Field(default=100, ge=1, le=300)
    force: bool = False
    settings: dict[str, object] = Field(default_factory=dict)


class GeneratedMixSaveRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)


# ---------------------------------------------------------------------------
# Playlists
# ---------------------------------------------------------------------------

class PlaylistCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    visibility: str = Field(default="private", pattern="^(public|private)$")
    track_ids: list[int] = Field(default_factory=list)


class PlaylistUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    visibility: str | None = Field(default=None, pattern="^(public|private)$")


class PlaylistTracksRequest(BaseModel):
    track_ids: list[int] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------

class FlowStartRequest(BaseModel):
    settings: dict[str, object] | None = None
    include_debug: bool = False


class FlowRefillRequest(BaseModel):
    session_id: str
    visible_buffer: int = Field(default=5, ge=1, le=50)
    region_id: str | None = None
    include_debug: bool = False


class FlowEventRequest(BaseModel):
    session_id: str
    event_type: str
    track_id: int | None = None
    artist_id: int | None = None
    release_id: int | None = None
