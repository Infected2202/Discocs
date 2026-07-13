from __future__ import annotations

import base64
import binascii
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
import json
import logging
import multiprocessing
import os
from pathlib import Path
import socket
import sqlite3
from threading import Thread
import time
from time import perf_counter
from datetime import UTC, datetime, timedelta
import traceback
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
import numpy as np
from starlette.background import BackgroundTask

from app.audio_features import AUDIO_FEATURE_EXTRACTOR, AudioFeatureAnalyzer
from app.audio_source import has_navidrome_audio_source, navidrome_item_id_for_track, track_audio_path
from app.autoplay import (
    autoplay_default_settings,
    refill_autoplay_queue,
)
from app.config import (
    DISCOGS_EFFNET_MODEL,
    MODEL_FILES,
    MUQ_MULAN_MODEL,
    Settings,
    load_runtime_settings,
    save_runtime_settings,
)
from app.embedder import DiscogsEffnetEmbedder, MuqMulanEmbedder, create_track_embedder
from app.head_pack import (
    DISCOGS_EFFNET_HEADS,
    DiscogsEffnetHeadPackAnalyzer,
    HeadOutput,
    download_model_file,
    download_head_pack_models,
    head_pack_readiness,
    required_model_files,
)
from app.logging_config import configure_logging, get_analysis_logger, get_navidrome_plugin_logger
from app.mixes import (
    dashboard_mix_generation_plan,
    ensure_dashboard_mixes,
    generate_mixes,
    generated_mix_default_settings,
)
from app.navidrome import NavidromeClient, artist_info_bio, artist_info_image_url
from app.navidrome_starred import (
    build_starred_catalog,
    build_starred_track_ids,
    ready_tracks_from_starred_catalog,
)
from app.navidrome_sync import sync_navidrome_catalog
from app.recommender import Recommender, build_index, index_metadata_path
from app.models import (
    AnalysisTask,
    Artist,
    ArtistSummaryRow,
    COMPLETION_FRACTION,
    EARLY_SKIP_FRACTION,
    EARLY_SKIP_SECONDS,
    FeatureFilter,
    FeatureTrack,
    InstantMixRequest,
    LATE_SKIP_FRACTION,
    MEANINGFUL_LISTEN_FRACTION,
    MEANINGFUL_LISTEN_SECONDS,
    ReleaseSummaryRow,
    ReleaseTrackRow,
    Track,
    TrackFeature,
    TrackPrediction,
    utc_now,
)
from app.store import (
    Store,
    row_to_track,
    playback_event_is_completion,
    similar_track_dict,
    track_dict,
    track_listing_dict,
)


configure_logging()
logger = logging.getLogger(__name__)
analysis_logger = get_analysis_logger()
navidrome_logger = logging.getLogger("discocs.navidrome")
navidrome_plugin_logger = get_navidrome_plugin_logger()
app = FastAPI(title="discocs", version="0.1.0")

from app.state import (  # noqa: E402  (import after app init to avoid circular issues)
    ACTIVE_JOB_STATUSES,
    ANALYZE_EXECUTORS,
    ANALYZE_EXECUTORS_LOCK,
    AUTO_INDEX_ANALYSIS_JOBS,
    AUTO_INDEX_LOCK,
    COVER_CACHE,
    COVER_CACHE_LOCK,
    COVER_CACHE_MAX_ITEMS,
    COVER_CACHE_TTL_SECONDS,
    COVER_ERROR_CACHE,
    COVER_ERROR_CACHE_TTL_SECONDS,
    COVER_TIMEOUT_SECONDS,
    DEFAULT_ANALYZE_TF_THREADS,
    DEFAULT_ANALYZE_WORKERS,
    DEFAULT_AUDIO_FEATURE_WORKERS,
    DEFERRED_JOB_ORDER,
    DEFERRED_JOB_STARTERS,
    DEFERRED_JOBS_LOCK,
    JOBS,
    JOBS_LOCK,
    MAINTENANCE_STOP,
    MAX_ANALYZE_TF_THREADS,
    MAX_ANALYZE_WORKERS,
    MAX_AUDIO_FEATURE_WORKERS,
    MAX_MIX_SEEDS,
    MIX_GENERATION_LOCK,
    SHUTDOWN_REQUESTED,
    STATS_CACHE,
    STATS_CACHE_LOCK,
    STATS_CACHE_TTL_SECONDS,
    TEXT_SEARCH_EMBEDDER,
    TEXT_SEARCH_EMBEDDER_LOCK,
    UI_BUILD_ID,
    WORKER_CONNECTED_TTL_SECONDS,
    WORKER_HEARTBEAT_WRITE_INTERVAL_SECONDS,
)


from app.schemas.responses import (  # noqa: E402
    ApiErrorDetail,
    ApiErrorResponse,
    ArtistAvailabilityStubResponse,
    ArtistDiscographyResponse,
    ArtistLinkResponse,
    ArtistResponse,
    ArtistSummaryResponse,
    AutoplayRefillResponse,
    AvailabilityStubResponse,
    DiscographyGroupResponse,
    EntityActionResponse,
    ImageInfoResponse,
    ImageRefResponse,
    LibraryStatsResponse,
    NavidromeSimilarItem,
    NavidromeSimilarResponse,
    PlaybackEventIngestResponse,
    PlaybackEventSummaryResponse,
    PlaybackQueueItemResponse,
    PlaybackQueueResponse,
    PlaybackSessionEnvelopeResponse,
    PlaybackSessionSummaryResponse,
    PlaybackSettingsResponse,
    RelatedDiscographyResponse,
    ReleaseAvailabilityStubResponse,
    ReleaseResponse,
    ReleaseSummaryResponse,
    ReleaseTrackItemResponse,
    ReleaseTracksResponse,
    SearchGroupResponse,
    SearchResponse,
    SearchTopResultResponse,
    TrackReleaseLinkResponse,
    TrackSummaryResponse,
)
from app.schemas.requests import (  # noqa: E402
    AnalyzeAudioFeaturesRequest,
    AnalyzeHeadsRequest,
    AnalyzeRequest,
    AutoplayRefillRequest,
    CancelJobRequest,
    DeleteAnalysisErrorsRequest,
    DeleteTracksRequest,
    FeedbackRequest,
    FeatureFilterRequest,
    FeatureSearchRequest,
    GeneratedMixSettingsRequest,
    IndexRequest,
    InstantMixSettingsRequest,
    MixGenerateRequest,
    NavidromePluginEventRequest,
    NavidromeSettingsRequest,
    NavidromeStarRequest,
    NavidromeSyncRequest,
    PlaybackEventRequest,
    PlaybackQueueItemRequest,
    PlaybackQueuePatchRequest,
    PlaybackSessionCreateRequest,
    PlaybackSessionPatchRequest,
    TextSearchRequest,
    WorkerClaimRequest,
    WorkerFailureItem,
    WorkerFailuresRequest,
    WorkerFeatureItem,
    WorkerFeatureResultItem,
    WorkerHeadOutputItem,
    WorkerHeadResultItem,
    WorkerPredictionItem,
    WorkerRegisterRequest,
    WorkerReleaseRequest,
    WorkerResultItem,
    WorkerSubmitRequest,
)

from app.serializers.entities import (  # noqa: E402
    _json_object,
    artist_link_dict,
    artist_summary_dict,
    artist_summary_with_external_image,
    entity_action,
    image_ref,
    release_summary_dict,
    release_track_dict,
    release_type_label,
    track_summary_dict,
)
from app.serializers.playback import (  # noqa: E402
    autoplay_pool_dict,
    build_initial_playback_queue,
    maybe_scrobble_navidrome_play,
    navidrome_scrobble_submission,
    playback_event_dict,
    playback_event_time_ms,
    playback_queue_dict,
    playback_session_dict,
    playback_session_response,
    queue_item_dict,
    queue_patch_items,
    should_scrobble_navidrome_play,
)
from app.serializers.mixes import (  # noqa: E402
    generated_mix_detail_dict,
    generated_mix_summary_dict,
)
from app.serializers.search import (  # noqa: E402
    _compact_artist_names,
    _discover_track_shelf_item,
    _release_shelf_item,
    _track_shelf_item,
    dashboard_shelf_item,
    search_group,
    search_top_result,
)

from app.analysis_helpers import (  # noqa: E402
    analysis_error_count,
    analysis_job_status_dict,
    analysis_task_dict,
    analysis_worker_dict,
    audio_feature_status,
    head_pack_status,
    recommender_index_status,
)
from app.services.analysis import terminate_process_pool  # noqa: E402

from app.services.jobs import (  # noqa: E402
    JobStatus,
    create_job,
    create_deferred_job_if_busy,
    finish_job,
    has_active_job,
    maybe_start_next_deferred_job,
    sync_memory_jobs_from_durable_jobs,
    update_job,
)
from app.services.cover import (  # noqa: E402
    cached_cover_error,
    cached_cover_response,
    cover_response,
    remember_cover,
    remember_cover_error,
)
from app.services.dashboard import (  # noqa: E402
    _dashboard_discover_random,
    _dashboard_generated_mixes,
    _dashboard_listen_again,
    _dashboard_long_time_no_listen,
    _dashboard_recently_added,
    dashboard_shelf_response,
    ensure_dashboard_mixes_fast,
)

from app.api.deps import (  # noqa: E402
    _bounded_int,
    _navidrome_client,
    _optional_bounded_float,
    api_error,
    context,
    generated_mix_settings,
    instant_mix_request_dict,
    instant_mix_result_dict,
    instant_mix_settings,
    model_to_dict,
    playback_session_settings,
    playback_settings_defaults,
    record_instant_mix_request,
    request_field_names,
    text_search_embedder,
)
from app.serializers.tracks import (  # noqa: E402
    audio_bitrate,
    audio_format,
    enriched_feature_track_dict,
    enriched_similar_track_dict,
    enriched_track_dict,
    enriched_track_listing_dict,
    feature_dict,
    feature_track_dict,
    first_prediction_dict,
    navidrome_raw_metadata,
    prediction_dict,
    track_card_metadata,
)

from app.api import (  # noqa: E402
    artists as _api_artists,
    auth as _api_auth,
    dashboard as _api_dashboard,
    metrics as _api_metrics,
    releases as _api_releases,
    search as _api_search,
    settings as _api_settings,
    mixes as _api_mixes,
    navidrome as _api_navidrome,
    tracks as _api_tracks,
    playback as _api_playback,
    workers as _api_workers,
    jobs as _api_jobs,
    playlists as _api_playlists,
    flow as _api_flow,
    map as _api_map,
)

app.include_router(_api_auth.router)
app.include_router(_api_dashboard.router)
app.include_router(_api_search.router)
app.include_router(_api_artists.router)
app.include_router(_api_releases.router)
app.include_router(_api_settings.router)
app.include_router(_api_metrics.router)
app.include_router(_api_playback.router)
app.include_router(_api_mixes.router)
app.include_router(_api_navidrome.router)
app.include_router(_api_tracks.router)
app.include_router(_api_workers.router)
app.include_router(_api_jobs.router)
app.include_router(_api_playlists.router)
app.include_router(_api_flow.router)
app.include_router(_api_map.router)



from app.api.middleware import (  # noqa: E402
    add_security_headers as _add_security_headers,
    log_http_request as _log_http_request,
)
from app.api.auth_middleware import auth_gate as _auth_gate  # noqa: E402
app.middleware("http")(_log_http_request)
app.middleware("http")(_auth_gate)
app.middleware("http")(_add_security_headers)

# Same-origin SPA/dev proxy need no CORS. DISCOCS_CORS_ORIGINS is the explicit
# credentialed allowlist for a separately hosted or native-webview client.
# The wildcard fallback exists only while auth is disabled for local legacy
# development; auth-enabled startup has no implicit cross-origin access.
_cors_origins_env = os.getenv("DISCOCS_CORS_ORIGINS", "").strip()
if _cors_origins_env:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in _cors_origins_env.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
elif os.getenv("DISCOCS_AUTH_ENABLED", "").strip().lower() not in {"1", "true", "yes", "on"}:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )





@app.exception_handler(RequestValidationError)
async def api_v1_validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
):
    if not request.url.path.startswith("/api/v1"):
        return await request_validation_exception_handler(request, exc)
    errors = exc.errors()
    message = "Invalid request"
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.get("loc", []) if part != "query")
        detail = str(first.get("msg") or message)
        message = f"{location}: {detail}" if location else detail
    return api_error(422, "invalid_request", message)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


from app.maintenance import start_maintenance_loop as _start_maintenance_loop  # noqa: E402

@app.on_event("startup")
def start_maintenance_loop() -> None:
    _start_maintenance_loop()


@app.on_event("shutdown")
def shutdown_analyze_workers() -> None:
    import app.state as _appstate
    logger.info("Application shutdown requested; terminating analyze workers")
    _appstate.SHUTDOWN_REQUESTED = True
    MAINTENANCE_STOP.set()
    with ANALYZE_EXECUTORS_LOCK:
        executors = list(ANALYZE_EXECUTORS)
    for executor in executors:
        terminate_process_pool(executor)


@app.get("/admin", response_class=HTMLResponse)
@app.get("/admin/search", response_class=HTMLResponse)
@app.get("/admin/artists/{artist_id}", response_class=HTMLResponse)
@app.get("/admin/releases/{release_id}", response_class=HTMLResponse)
@app.get("/admin/mixes/{mix_id}", response_class=HTMLResponse)
@app.get("/admin/settings", response_class=HTMLResponse)
def admin_ui() -> HTMLResponse:
    return HTMLResponse(
        UI_HTML,
        headers={
            "Cache-Control": "no-store",
            "X-Discocs-UI-Build": UI_BUILD_ID,
        },
    )



@app.get("/debug/ui")
def debug_ui() -> dict[str, object]:
    return {
        "build": UI_BUILD_ID,
        "likes_source": "navidrome",
        "frontend_likes_cache": False,
    }


UI_HTML = (Path(__file__).parent / "ui.html").read_text()
