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
from threading import Event, Lock, Thread
import time
from time import perf_counter
from datetime import UTC, datetime
import traceback
from typing import Callable
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
import numpy as np
from starlette.background import BackgroundTask

from app.audio_features import AUDIO_FEATURE_EXTRACTOR, AudioFeatureAnalyzer
from app.audio_source import is_navidrome_track, track_audio_path
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
from app.navidrome import NavidromeClient
from app.navidrome_starred import (
    build_starred_catalog,
    build_starred_track_ids,
    ready_tracks_from_starred_catalog,
)
from app.navidrome_sync import sync_navidrome_catalog
from app.recommender import Recommender, build_index, index_metadata_path
from app.store import (
    AnalysisTask,
    Artist,
    ArtistSummaryRow,
    FeatureFilter,
    FeatureTrack,
    InstantMixRequest,
    ReleaseSummaryRow,
    ReleaseTrackRow,
    Store,
    Track,
    TrackFeature,
    TrackPrediction,
    similar_track_dict,
    track_dict,
    track_listing_dict,
    utc_now,
)


configure_logging()
logger = logging.getLogger(__name__)
analysis_logger = get_analysis_logger()
navidrome_logger = logging.getLogger("discocs.navidrome")
navidrome_plugin_logger = get_navidrome_plugin_logger()
app = FastAPI(title="discocs", version="0.1.0")
MAINTENANCE_STOP = Event()
JOBS_LOCK = Lock()
JOBS: dict[str, "JobStatus"] = {}
DEFERRED_JOBS_LOCK = Lock()
DEFERRED_JOB_ORDER: list[str] = []
DEFERRED_JOB_STARTERS: dict[str, Callable[[], None]] = {}
ANALYZE_EXECUTORS_LOCK = Lock()
ANALYZE_EXECUTORS: set[ProcessPoolExecutor] = set()
SHUTDOWN_REQUESTED = False
MAX_MIX_SEEDS = 50
MAX_ANALYZE_WORKERS = max(1, os.cpu_count() or 1)
DEFAULT_ANALYZE_WORKERS = min(4, MAX_ANALYZE_WORKERS)
MAX_ANALYZE_TF_THREADS = MAX_ANALYZE_WORKERS
DEFAULT_ANALYZE_TF_THREADS = min(4, MAX_ANALYZE_TF_THREADS)
MAX_AUDIO_FEATURE_WORKERS = max(32, MAX_ANALYZE_WORKERS)
DEFAULT_AUDIO_FEATURE_WORKERS = min(8, MAX_AUDIO_FEATURE_WORKERS)
COVER_TIMEOUT_SECONDS = 5
WORKER_HEARTBEAT_WRITE_INTERVAL_SECONDS = 60
WORKER_CONNECTED_TTL_SECONDS = WORKER_HEARTBEAT_WRITE_INTERVAL_SECONDS * 3
COVER_CACHE_TTL_SECONDS = 3600
COVER_ERROR_CACHE_TTL_SECONDS = 300
COVER_CACHE_MAX_ITEMS = 512
COVER_CACHE_LOCK = Lock()
COVER_CACHE: dict[tuple[str, int], tuple[float, bytes, str]] = {}
COVER_ERROR_CACHE: dict[tuple[str, int], tuple[float, str]] = {}
STATS_CACHE_TTL_SECONDS = 10
STATS_CACHE_LOCK = Lock()
STATS_CACHE: dict[str, tuple[float, dict[str, object]]] = {}
AUTO_INDEX_LOCK = Lock()
AUTO_INDEX_ANALYSIS_JOBS: set[str] = set()
ACTIVE_JOB_STATUSES = {"queued", "running"}
TEXT_SEARCH_EMBEDDER_LOCK = Lock()
TEXT_SEARCH_EMBEDDER: MuqMulanEmbedder | None = None
UI_BUILD_ID = "likes-remote-only-20260611-1918"


def should_log_http_request(path: str) -> bool:
    if path in {"/stats", "/jobs"}:
        return True
    if path.startswith(("/metrics", "/navidrome", "/instant-mix", "/text-search")):
        return True
    return path.startswith("/tracks/") and (
        path.endswith("/cover")
        or path.endswith("/similar")
        or path.endswith("/navidrome-star")
    )


@app.middleware("http")
async def log_http_request(request: Request, call_next):
    path = request.url.path
    should_log = should_log_http_request(path)
    started = perf_counter()
    if should_log:
        logger.info(
            "HTTP request started method=%s path=%s query=%s",
            request.method,
            path,
            request.url.query,
        )
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "HTTP request failed method=%s path=%s query=%s seconds=%.3f",
            request.method,
            path,
            request.url.query,
            perf_counter() - started,
        )
        raise
    seconds = perf_counter() - started
    if should_log or seconds >= 1.0:
        logger.info(
            "HTTP request completed method=%s path=%s status=%s seconds=%.3f",
            request.method,
            path,
            response.status_code,
            seconds,
        )
    return response


@dataclass
class JobStatus:
    id: str
    kind: str
    status: str
    message: str
    total: int | None = None
    done: int = 0
    failed: int = 0
    current: str | None = None
    elapsed_seconds: float = 0.0
    tracks_per_min: float | None = None
    eta_seconds: float | None = None
    error_detail: str | None = None
    started_at: float = 0.0
    created_at_epoch: float = 0.0
    finished_at: float | None = None


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
    execution_mode: str = Field(default="both", pattern="^(both|local|remote)$")


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
    execution_mode: str = Field(default="both", pattern="^(both|local|remote)$")


class AnalyzeAudioFeaturesRequest(BaseModel):
    limit: int | None = Field(default=None, ge=1)
    workers: int = Field(default=DEFAULT_AUDIO_FEATURE_WORKERS, ge=1, le=MAX_AUDIO_FEATURE_WORKERS)
    local_executor_enabled: bool = True
    max_attempts: int = Field(default=3, ge=1, le=20)
    execution_mode: str = Field(default="both", pattern="^(both|local|remote)$")
    reset_existing: bool = False
    extractor: str = AUDIO_FEATURE_EXTRACTOR


class DeleteTracksRequest(BaseModel):
    track_ids: list[int] = Field(default_factory=list)
    all_missing: bool = False


class DeleteAnalysisErrorsRequest(BaseModel):
    task_ids: list[str] = Field(default_factory=list)
    all_errors: bool = False


class IndexRequest(BaseModel):
    model: str = "discogs_multi"


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


class NavidromeSimilarItem(BaseModel):
    item_id: str
    track_id: int
    artist: str | None = None
    title: str | None = None
    album: str | None = None
    distance: float
    similarity: float


class NavidromeSimilarResponse(BaseModel):
    provider: str = "navidrome"
    request_id: str
    seed_item_id: str
    seed_track_id: int
    model: str
    requested_count: int | None = None
    effective_count: int
    min_similarity: float | None = None
    skipped_without_external_id: int = 0
    results: list[NavidromeSimilarItem]


class InstantMixSettingsRequest(BaseModel):
    model: str = "discogs_multi"
    count: int = Field(default=50, ge=1, le=500)
    min_similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    max_per_artist: int = Field(default=2, ge=1, le=100)
    exclude_same_album: bool = True
    count_collaboration_artists: bool = True


class TextSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    count: int = Field(default=50, ge=1, le=500)
    min_similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    max_per_artist: int = Field(default=2, ge=1, le=100)
    exclude_same_album: bool = True
    count_collaboration_artists: bool = True


class NavidromePluginEventRequest(BaseModel):
    event: str
    item_id: str | None = None
    model: str | None = None
    count: int | None = None
    status: int | None = None
    discocs_url: str | None = None
    message: str | None = None


class FeedbackRequest(BaseModel):
    seed_track_id: int
    result_track_id: int
    model: str = "discogs_multi"
    rating: int
    note: str | None = None


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


def context() -> tuple[Store, Settings]:
    settings = Settings.from_env()
    store = Store(settings.db_path)
    store.init()
    return store, settings


def instant_mix_settings(settings: Settings) -> dict[str, object]:
    saved = load_runtime_settings(settings.data_dir).get("instant_mix", {})
    saved = saved if isinstance(saved, dict) else {}
    return {
        "model": str(saved.get("model") or "discogs_multi"),
        "count": _bounded_int(saved.get("count"), default=50, minimum=1, maximum=500),
        "min_similarity": _optional_bounded_float(
            saved.get("min_similarity"),
            default=None,
            minimum=0.0,
            maximum=1.0,
        ),
        "max_per_artist": _bounded_int(
            saved.get("max_per_artist"),
            default=2,
            minimum=1,
            maximum=100,
        ),
        "exclude_same_album": bool(saved.get("exclude_same_album", True)),
        "count_collaboration_artists": bool(saved.get("count_collaboration_artists", True)),
    }


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _optional_bounded_float(
    value: object,
    *,
    default: float | None,
    minimum: float,
    maximum: float,
) -> float | None:
    if value is None:
        return default
    if value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def instant_mix_result_dict(
    store: Store,
    raw: dict[str, object],
    ratings: dict[int, int],
) -> dict[str, object]:
    track_id = raw.get("track_id")
    try:
        parsed_track_id = int(track_id) if track_id is not None else None
    except (TypeError, ValueError):
        parsed_track_id = None
    if parsed_track_id is None:
        return raw
    track = store.get_track(parsed_track_id)
    if track is None:
        return raw
    data = enriched_track_dict(store, track)
    data["item_id"] = raw.get("item_id")
    data["distance"] = raw.get("distance")
    data["similarity"] = raw.get("similarity")
    data["rating"] = ratings.get(parsed_track_id)
    data["has_embedding"] = True
    return data


def instant_mix_request_dict(
    request: InstantMixRequest,
    *,
    include_results: bool,
    store: Store | None = None,
) -> dict[str, object]:
    data: dict[str, object] = {
        "id": request.id,
        "provider": request.provider,
        "seed_item_id": request.seed_item_id,
        "seed_track_id": request.seed_track_id,
        "model": request.model_name,
        "requested_count": request.requested_count,
        "effective_count": request.effective_count,
        "max_per_artist": request.max_per_artist,
        "exclude_same_album": request.exclude_same_album,
        "min_similarity": request.min_similarity,
        "status": request.status,
        "result_count": request.result_count,
        "skipped_without_external_id": request.skipped_without_external_id,
        "duration_ms": request.duration_ms,
        "error": request.error,
        "created_at": request.created_at,
        "params": json.loads(request.params_json or "{}"),
    }
    if store is not None and request.seed_track_id is not None:
        seed_track = store.get_track(request.seed_track_id)
        if seed_track is not None:
            data["seed_track"] = enriched_track_dict(store, seed_track)
    if include_results:
        results = json.loads(request.results_json or "[]")
        if store is not None and request.seed_track_id is not None:
            ratings = store.feedback_for_seed(request.seed_track_id, request.model_name)
            results = [
                instant_mix_result_dict(store, result, ratings)
                if isinstance(result, dict)
                else result
                for result in results
            ]
        data["results"] = results
    return data


def record_instant_mix_request(
    store: Store,
    *,
    request_id: str,
    item_id: str,
    seed_track_id: int | None,
    model: str,
    requested_model: str | None,
    requested_count: int | None,
    effective_count: int,
    max_per_artist: int,
    exclude_same_album: bool,
    count_collaboration_artists: bool,
    min_similarity: float | None,
    status: str,
    results: list[NavidromeSimilarItem],
    skipped_without_external_id: int,
    duration_ms: float | None,
    requested_max_per_artist: int | None = None,
    requested_exclude_same_album: bool | None = None,
    error: str | None = None,
) -> None:
    params = {
        "requested_model": requested_model,
        "effective_model": model,
        "requested_count": requested_count,
        "requested_max_per_artist": requested_max_per_artist,
        "requested_exclude_same_album": requested_exclude_same_album,
        "effective_count": effective_count,
        "max_per_artist": max_per_artist,
        "exclude_same_album": exclude_same_album,
        "count_collaboration_artists": count_collaboration_artists,
        "min_similarity": min_similarity,
    }
    store.record_instant_mix_request(
        request_id=request_id,
        provider="navidrome",
        seed_item_id=item_id,
        seed_track_id=seed_track_id,
        model_name=model,
        requested_count=requested_count,
        effective_count=effective_count,
        max_per_artist=max_per_artist,
        exclude_same_album=exclude_same_album,
        min_similarity=min_similarity,
        status=status,
        result_count=len(results),
        skipped_without_external_id=skipped_without_external_id,
        duration_ms=duration_ms,
        error=error,
        params_json=json.dumps(params, ensure_ascii=True, sort_keys=True),
        results_json=json.dumps([model_to_dict(item) for item in results], ensure_ascii=True),
    )


def model_to_dict(model: BaseModel) -> dict[str, object]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def api_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def image_ref(url: str | None, source: str = "none") -> dict[str, object]:
    return {"url": url, "source": source if url else "none", "placeholder": url is None}


def entity_action(action_type: str, enabled: bool = True, endpoint: str | None = None) -> dict[str, object]:
    return {"type": action_type, "enabled": enabled, "endpoint": endpoint}


def release_type_label(release_type: str) -> str:
    return {
        "album": "Album",
        "ep": "EP",
        "single": "Single",
        "compilation": "Compilation",
        "soundtrack": "Soundtrack",
        "mix": "Mix",
    }.get(release_type, "Release")


def artist_summary_dict(row: ArtistSummaryRow | Artist) -> dict[str, object]:
    artist = row.artist if isinstance(row, ArtistSummaryRow) else row
    track_count = row.track_count if isinstance(row, ArtistSummaryRow) else 0
    release_count = row.release_count if isinstance(row, ArtistSummaryRow) else 0
    return {
        "id": artist.id,
        "name": artist.name,
        "image": image_ref(artist.image_url, "external" if artist.image_url else "none"),
        "library_stats": {
            "tracks": track_count,
            "releases": release_count,
            "liked_tracks": 0,
            "plays": 0,
        },
    }


def artist_link_dict(artist: Artist) -> dict[str, object]:
    return {"id": artist.id, "name": artist.name}


def release_summary_dict(row: ReleaseSummaryRow) -> dict[str, object]:
    release = row.release
    cover_url = f"/api/v1/releases/{release.id}/cover" if release.cover_art_id else None
    return {
        "id": release.id,
        "title": release.title,
        "release_type": release.release_type,
        "release_type_label": release_type_label(release.release_type),
        "artists": [artist_link_dict(artist) for artist in row.artists],
        "release_date": release.release_date,
        "release_year": release.release_year,
        "track_count": release.track_count,
        "duration": release.duration,
        "artwork": image_ref(cover_url, "navidrome" if cover_url else "none"),
    }


def track_summary_dict(store: Store, track: Track, artists: list[Artist] | None = None) -> dict[str, object]:
    release = _track_release_summary(store, track.id)
    return {
        "id": track.id,
        "title": track.title or Path(track.path).stem,
        "artists": [artist_link_dict(artist) for artist in (artists or [])],
        "duration": track.duration,
        "release": release,
        "artwork": image_ref(f"/tracks/{track.id}/cover", "local"),
        "explicit": False,
        "liked": False,
        "actions": [],
    }


def release_track_dict(store: Store, item: ReleaseTrackRow) -> dict[str, object]:
    data = track_summary_dict(store, item.track, item.artists)
    data.update(
        {
            "disc_number": item.disc_number,
            "track_number": item.track_number,
            "position": item.position,
        }
    )
    return data


def _track_release_summary(store: Store, track_id: int) -> dict[str, object] | None:
    with store.connect() as conn:
        row = conn.execute(
            """
            SELECT r.id, r.title
            FROM release_tracks rt
            JOIN releases r ON r.id = rt.release_id
            WHERE rt.track_id = ?
            ORDER BY rt.position
            LIMIT 1
            """,
            (track_id,),
        ).fetchone()
    if row is None:
        return None
    return {"id": int(row["id"]), "title": str(row["title"])}


def search_group(group_type: str, title: str, items: list[dict[str, object]], total: int, limit: int, offset: int) -> dict[str, object]:
    next_offset = offset + limit if offset + limit < total else None
    return {
        "type": group_type,
        "title": title,
        "items": items,
        "total": total,
        "next_offset": next_offset,
    }


def create_job(kind: str, message: str) -> str:
    job_id = str(uuid4())
    now_epoch = time.time()
    with JOBS_LOCK:
        JOBS[job_id] = JobStatus(
            id=job_id,
            kind=kind,
            status="queued",
            message=message,
            started_at=perf_counter(),
            created_at_epoch=now_epoch,
        )
    logger.info("Created job job_id=%s kind=%s message=%s", job_id, kind, message)
    return job_id


def update_job(job_id: str, **changes: object) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        for key, value in changes.items():
            setattr(job, key, value)


def finish_job(job_id: str, status: str, message: str, error_detail: str | None = None) -> None:
    finished_at = perf_counter()
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        elapsed_seconds = max(0.0, finished_at - job.started_at) if job else 0.0
    update_job(
        job_id,
        status=status,
        message=message,
        current=None,
        elapsed_seconds=elapsed_seconds,
        eta_seconds=None,
        error_detail=error_detail,
        finished_at=finished_at,
    )
    log = logger.error if status == "failed" else logger.info
    log("Finished job job_id=%s status=%s message=%s", job_id, status, message)
    maybe_start_next_deferred_job()


def has_active_job(store: Store | None = None) -> bool:
    with JOBS_LOCK:
        if any(job.status in {"queued", "running"} for job in JOBS.values()):
            return True
    if store is None:
        try:
            store, _settings = context()
        except Exception:
            logger.exception("Failed to inspect durable jobs while checking active queue state")
            return True
    return store.has_active_analysis_job()


def create_deferred_job_if_busy(
    kind: str,
    message: str,
    starter_factory: Callable[[str], Callable[[], None]],
    *,
    store: Store | None = None,
) -> tuple[str | None, bool]:
    with DEFERRED_JOBS_LOCK:
        if not has_active_job(store):
            return None, False
        job_id = create_job(kind, message)
        DEFERRED_JOB_STARTERS[job_id] = starter_factory(job_id)
        DEFERRED_JOB_ORDER.append(job_id)
    update_job(
        job_id,
        status="deferred",
        message=f"Waiting for previous job: {message}",
        current=None,
    )
    logger.info("Deferred job job_id=%s kind=%s message=%s", job_id, kind, message)
    return job_id, True


def _run_deferred_job(job_id: str) -> None:
    with DEFERRED_JOBS_LOCK:
        starter = DEFERRED_JOB_STARTERS.get(job_id)
    if starter is None:
        return
    try:
        starter()
    finally:
        with DEFERRED_JOBS_LOCK:
            DEFERRED_JOB_STARTERS.pop(job_id, None)
            if job_id in DEFERRED_JOB_ORDER:
                DEFERRED_JOB_ORDER.remove(job_id)
        maybe_start_next_deferred_job()


def maybe_start_next_deferred_job() -> str | None:
    with DEFERRED_JOBS_LOCK:
        if has_active_job():
            return None
        next_job_id = None
        for candidate in list(DEFERRED_JOB_ORDER):
            with JOBS_LOCK:
                job = JOBS.get(candidate)
                is_waiting = job is not None and job.status == "deferred"
            if candidate in DEFERRED_JOB_STARTERS and is_waiting:
                next_job_id = candidate
                DEFERRED_JOB_ORDER.remove(candidate)
                break
            DEFERRED_JOB_STARTERS.pop(candidate, None)
            DEFERRED_JOB_ORDER.remove(candidate)
    if next_job_id is None:
        return None
    update_job(
        next_job_id,
        status="queued",
        message="Starting deferred job",
        current=None,
        started_at=perf_counter(),
    )
    Thread(target=_run_deferred_job, args=(next_job_id,), daemon=True).start()
    logger.info("Started deferred job job_id=%s", next_job_id)
    return next_job_id


def sync_memory_jobs_from_durable_jobs(jobs: list[object]) -> None:
    with JOBS_LOCK:
        for durable_job in jobs:
            memory_job = JOBS.get(durable_job.id)
            if memory_job is None:
                continue
            memory_job.status = durable_job.status
            memory_job.message = durable_job.message
            memory_job.total = durable_job.total
            memory_job.done = durable_job.done
            memory_job.failed = durable_job.failed
            if durable_job.status not in {"queued", "running"}:
                memory_job.current = None
                memory_job.eta_seconds = None
                if memory_job.finished_at is None:
                    memory_job.finished_at = perf_counter()


def schedule_auto_index_for_analysis(
    store: Store,
    analysis_job_id: str,
    background_tasks: BackgroundTasks | None = None,
) -> str | None:
    job = store.get_analysis_job(analysis_job_id)
    if job is None or job.kind != "analyze" or job.status != "completed" or job.done <= 0:
        return None
    with AUTO_INDEX_LOCK:
        if analysis_job_id in AUTO_INDEX_ANALYSIS_JOBS:
            return None
        AUTO_INDEX_ANALYSIS_JOBS.add(analysis_job_id)

    deferred_job_id, deferred = create_deferred_job_if_busy(
        "index",
        f"Waiting to rebuild index for {job.model_name} after analyze",
        lambda job_id: lambda: _index_job(job_id, job.model_name),
        store=store,
    )
    if deferred:
        logger.info(
            "Deferred auto index job analysis_job_id=%s index_job_id=%s model=%s",
            analysis_job_id,
            deferred_job_id,
            job.model_name,
        )
        return deferred_job_id

    index_job_id = create_job(
        "index",
        f"Waiting to rebuild index for {job.model_name} after analyze",
    )
    logger.info(
        "Scheduled auto index job analysis_job_id=%s index_job_id=%s model=%s",
        analysis_job_id,
        index_job_id,
        job.model_name,
    )
    if background_tasks is not None:
        background_tasks.add_task(_index_job, index_job_id, job.model_name)
    else:
        _index_job(index_job_id, job.model_name)
    return index_job_id


def exception_detail(exc: Exception) -> str:
    parts = [
        "".join(traceback.format_exception_only(type(exc), exc)).strip(),
        f"repr: {exc!r}",
        f"args: {exc.args!r}",
    ]
    reason = getattr(exc, "reason", None)
    if reason is not None:
        parts.append(f"reason: {reason!r}")
    return "\n".join(part for part in parts if part)


def exception_traceback(exc: Exception) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()


def is_sqlite_locked(exc: Exception) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "database is locked" in str(exc).lower()


def is_sqlite_disk_io_error(exc: Exception) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "disk i/o error" in str(exc).lower()


def sqlite_retry(operation, *, attempts: int = 8):
    delay = 0.05
    for attempt in range(attempts):
        try:
            return operation()
        except sqlite3.OperationalError as exc:
            if not is_sqlite_locked(exc) or attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 1.0)
    raise RuntimeError("unreachable sqlite retry state")


def raise_worker_sqlite_http_exception(exc: sqlite3.OperationalError, action: str) -> None:
    if is_sqlite_locked(exc):
        raise HTTPException(status_code=503, detail=f"SQLite is busy; retry worker {action}") from exc
    if is_sqlite_disk_io_error(exc):
        logger.error("SQLite disk I/O error during worker %s", action, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="SQLite disk I/O error; check database storage and retry",
        ) from exc
    raise exc


def download_failure_hint(exc: Exception) -> str | None:
    reason = getattr(exc, "reason", None)
    if isinstance(reason, socket.gaierror):
        return (
            "DNS lookup failed in the server runtime. Check network/DNS access from the "
            "machine or container running discocs, or place the model files in models/ manually."
        )
    return None


def analyze_progress(started_at: float, total: int, done: int, failed: int) -> dict[str, object]:
    elapsed = max(0.0, perf_counter() - started_at)
    completed = done + failed
    tracks_per_min = (completed / elapsed) * 60 if elapsed > 0 and completed > 0 else None
    eta_seconds = None
    if tracks_per_min and total > completed:
        eta_seconds = ((total - completed) / tracks_per_min) * 60
    return {
        "elapsed_seconds": elapsed,
        "tracks_per_min": tracks_per_min,
        "eta_seconds": eta_seconds,
    }


def head_pack_status(store: Store, settings: Settings) -> dict[str, object]:
    readiness = head_pack_readiness(settings)
    head_names = [head.id for head in DISCOGS_EFFNET_HEADS]
    tracks = store.count_tracks()
    saved_outputs = store.count_model_outputs()
    expected_outputs = tracks * len(head_names)
    missing_tracks = store.count_tracks_missing_head_pack(head_names)
    return {
        **readiness,
        "track_count": tracks,
        "expected_outputs": expected_outputs,
        "saved_outputs": saved_outputs,
        "complete_tracks": max(tracks - missing_tracks, 0),
        "missing_tracks": missing_tracks,
        "per_head_output_counts": store.count_model_outputs_by_model(head_names),
    }


def audio_feature_status(store: Store) -> dict[str, object]:
    tracks = store.count_tracks()
    complete = store.count_feature_tracks(AUDIO_FEATURE_EXTRACTOR)
    missing = store.count_tracks_missing_features(AUDIO_FEATURE_EXTRACTOR)
    return {
        "extractor": AUDIO_FEATURE_EXTRACTOR,
        "track_count": tracks,
        "complete_tracks": complete,
        "missing_tracks": missing,
    }


def recommender_index_status(
    settings: Settings,
    model: str,
    embedding_count: int,
) -> dict[str, object]:
    path = settings.index_path(model)
    metadata_path = index_metadata_path(path)
    metadata: dict[str, object] = {}
    if metadata_path.exists():
        try:
            loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            metadata = {}

    raw_index_count = metadata.get("count")
    try:
        index_count = int(raw_index_count) if raw_index_count is not None else None
    except (TypeError, ValueError):
        index_count = None

    exists = path.exists()
    metadata_matches_model = metadata.get("model_name") == model
    if not exists:
        status = "missing"
    elif index_count is None or not metadata_matches_model:
        status = "unknown"
    elif index_count == embedding_count:
        status = "ready"
    else:
        status = "stale"

    return {
        "status": status,
        "exists": exists,
        "path": str(path),
        "metadata_path": str(metadata_path),
        "metadata_exists": metadata_path.exists(),
        "count": index_count,
        "embedding_count": embedding_count,
        "stale": status in {"stale", "unknown"},
    }


def analysis_error_count(store: Store) -> int:
    where = """
        t.error IS NOT NULL
        AND COALESCE(t.error_type, '') != 'Cancelled'
        AND COALESCE(t.stage, '') != 'cancelled'
        AND t.error NOT LIKE 'Model file not found:%'
    """
    with store.connect() as conn:
        return conn.execute(
            f"""
            SELECT COUNT(*) FROM (
                SELECT t.track_id, t.model_name
                FROM analysis_tasks t
                JOIN tracks tr ON tr.id = t.track_id
                WHERE {where}
                GROUP BY t.track_id, t.model_name
            )
            """
        ).fetchone()[0]


def prediction_dict(prediction) -> dict[str, object]:
    return {
        "label": prediction.label,
        "score": prediction.score,
        "rank": prediction.rank,
    }


def feature_dict(feature) -> dict[str, object]:
    return {
        "name": feature.name,
        "value": feature.value,
        "text_value": feature.text_value,
        "unit": feature.unit,
        "confidence": feature.confidence,
        "extractor": feature.extractor,
    }


def feature_track_dict(item: FeatureTrack) -> dict[str, object]:
    data = track_dict(item.track)
    data["features"] = [feature_dict(feature) for feature in item.features]
    return data


def enriched_feature_track_dict(store: Store, item: FeatureTrack) -> dict[str, object]:
    data = feature_track_dict(item)
    data.update(track_card_metadata(store, item.track))
    return data


def track_card_metadata(store: Store, track: Track) -> dict[str, object]:
    features = store.load_features(track.id, AUDIO_FEATURE_EXTRACTOR)
    feature_by_name = {feature.name: feature for feature in features}
    navidrome_item_id = store.external_id_for_track("navidrome", track.id)
    raw = navidrome_raw_metadata(store, track)
    return {
        "navidrome_item_id": navidrome_item_id,
        "card_features": {
            name: feature_dict(feature)
            for name, feature in feature_by_name.items()
            if name in {"bpm", "key", "scale"}
        },
        "genre_discogs400": [
            prediction_dict(prediction)
            for prediction in store.load_predictions(track.id, "genre_discogs400", limit=3)
        ],
        "approachability_3c": first_prediction_dict(
            store.load_predictions(track.id, "approachability_3c", limit=3),
            "approachable",
        ),
        "engagement_3c": first_prediction_dict(
            store.load_predictions(track.id, "engagement_3c", limit=3),
            "engaging",
        ),
        "audio_format": audio_format(track, raw),
        "bitrate": audio_bitrate(track, raw),
    }


def first_prediction_dict(
    predictions: list[TrackPrediction],
    preferred_label: str,
) -> dict[str, object] | None:
    preferred = preferred_label.strip().lower()
    for prediction in predictions:
        if prediction.label.strip().lower() == preferred:
            return prediction_dict(prediction)
    return None


def navidrome_raw_metadata(store: Store, track: Track) -> dict[str, object]:
    external_id = store.external_id_for_track("navidrome", track.id)
    if external_id is None:
        return {}
    mapping = store.get_external_track("navidrome", external_id)
    if mapping is None or not mapping.raw_json:
        return {}
    try:
        parsed = json.loads(mapping.raw_json)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def audio_format(track: Track, raw: dict[str, object]) -> str | None:
    suffix = raw.get("suffix") or raw.get("format")
    if suffix:
        return str(suffix).strip(". ").upper() or None
    content_type = raw.get("contentType") or raw.get("content_type")
    if content_type:
        clean = str(content_type).split("/")[-1].strip()
        return clean.upper() if clean else None
    path_suffix = Path(track.path).suffix
    return path_suffix.strip(".").upper() if path_suffix else None


def audio_bitrate(track: Track, raw: dict[str, object]) -> int | None:
    for key in ("bitRate", "bitrate", "bit_rate"):
        value = raw.get(key)
        if value is None:
            continue
        try:
            bitrate = int(float(value))
        except (TypeError, ValueError):
            continue
        if bitrate > 0:
            return bitrate
    if track.duration and track.file_size > 0:
        return max(1, round((track.file_size * 8) / (float(track.duration) * 1000)))
    return None


def enriched_track_dict(store: Store, track: Track) -> dict[str, object]:
    data = track_dict(track)
    data.update(track_card_metadata(store, track))
    return data


def enriched_track_listing_dict(store: Store, listing) -> dict[str, object]:
    data = track_listing_dict(listing)
    data["navidrome_item_id"] = store.external_id_for_track("navidrome", listing.track.id)
    return data


def enriched_similar_track_dict(store: Store, result) -> dict[str, object]:
    data = similar_track_dict(result)
    data.update(track_card_metadata(store, result.track))
    return data


def text_search_embedder(settings: Settings) -> MuqMulanEmbedder:
    global TEXT_SEARCH_EMBEDDER
    with TEXT_SEARCH_EMBEDDER_LOCK:
        if TEXT_SEARCH_EMBEDDER is None:
            TEXT_SEARCH_EMBEDDER = MuqMulanEmbedder(settings)
        return TEXT_SEARCH_EMBEDDER


def analysis_task_dict(task: AnalysisTask) -> dict[str, object]:
    return {
        "task_id": task.id,
        "job_id": task.job_id,
        "track_id": task.track_id,
        "model_name": task.model_name,
        "status": task.status,
        "attempts": task.attempts,
        "max_attempts": task.max_attempts,
        "lease_owner": task.lease_owner,
        "lease_expires_at": task.lease_expires_at,
        "path": task.path,
        "file_size": task.file_size,
        "mtime": task.mtime,
        "error": task.error,
        "error_type": task.error_type,
        "stage": task.stage,
        "audio_url": f"/workers/tasks/{task.id}/audio",
    }


def timestamp_from_iso(value: str) -> float:
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


def analysis_worker_age_seconds(worker, now: datetime | None = None) -> float | None:
    try:
        last_seen = datetime.fromisoformat(worker.last_seen_at)
    except (TypeError, ValueError):
        return None
    current = now or datetime.now(last_seen.tzinfo)
    return max(0.0, (current - last_seen).total_seconds())


def analysis_worker_connected(worker, now: datetime | None = None) -> bool:
    age = analysis_worker_age_seconds(worker, now)
    return worker.status == "online" and age is not None and age <= WORKER_CONNECTED_TTL_SECONDS


def analysis_job_status_dict(
    job,
    store: Store | None = None,
    *,
    detail: bool = True,
    workers: list[object] | None = None,
) -> dict[str, object]:
    completed = job.done + job.failed
    started_at = timestamp_from_iso(job.created_at)
    now_ts = datetime.now().timestamp()
    finished_at = timestamp_from_iso(job.finished_at) if job.finished_at else None
    terminal = job.status not in {"queued", "running"}
    elapsed_until = finished_at or (timestamp_from_iso(job.updated_at) if terminal else now_ts)
    elapsed_seconds = max(0.0, elapsed_until - started_at) if started_at else 0.0
    tracks_per_min = (
        (completed / elapsed_seconds) * 60
        if job.status == "running" and elapsed_seconds > 0 and completed > 0
        else None
    )
    recent_tracks_per_min = None
    recent_window_seconds = None
    recent_completed = None
    if detail and store is not None and job.status == "running" and started_at:
        recent_window_seconds = min(300.0, max(1.0, now_ts - started_at))
        recent_since = datetime.fromtimestamp(now_ts - recent_window_seconds, UTC).isoformat()
        recent_completed = store.count_recent_finished_analysis_tasks(job.id, recent_since)
        if recent_completed > 0:
            recent_tracks_per_min = (recent_completed / recent_window_seconds) * 60
    eta_seconds = None
    eta_rate = recent_tracks_per_min or tracks_per_min
    if eta_rate and job.total > completed and job.status == "running":
        eta_seconds = ((job.total - completed) / eta_rate) * 60
    task_summary = store.analysis_job_task_summary(job.id) if detail and store is not None else {}
    if detail and workers is None and store is not None:
        workers = store.list_analysis_workers()
    workers = workers or []
    connected_workers = [worker for worker in workers if analysis_worker_connected(worker)]
    supporting_workers = [
        worker.worker_id
        for worker in connected_workers
        if job.model_name in {model for model in worker.models.split(",") if model}
    ]
    online_workers = [
        {
            "worker_id": worker.worker_id,
            "models": [model for model in worker.models.split(",") if model],
            "stage": worker.stage,
            "message": worker.message,
            "last_seen_at": worker.last_seen_at,
        }
        for worker in connected_workers
    ]
    status_hint = ""
    if detail and job.status == "running":
        leased_workers = task_summary.get("leased_workers", [])
        if job.leased and leased_workers:
            labels = ", ".join(
                f"{item['worker_id']}({item['count']})" for item in leased_workers
            )
            status_hint = f"Processing on {labels}; queued {job.queued}, leased {job.leased}"
        elif job.queued:
            if supporting_workers:
                status_hint = (
                    f"Queued {job.queued}; waiting for claim by "
                    f"{', '.join(supporting_workers)}"
                )
            else:
                worker_labels = [
                    f"{worker['worker_id']}[{', '.join(worker['models'])}]"
                    for worker in online_workers
                ]
                suffix = f" Online workers: {', '.join(worker_labels)}" if worker_labels else " No workers online."
                status_hint = f"Waiting for worker supporting {job.model_name}.{suffix}"
    recent_errors = task_summary.get("recent_errors", [])
    oldest_lease = task_summary.get("oldest_lease")
    oldest_lease_age = None
    if isinstance(oldest_lease, dict) and oldest_lease.get("updated_at"):
        oldest_lease_age = max(0.0, datetime.now().timestamp() - timestamp_from_iso(str(oldest_lease["updated_at"])))
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "message": job.message,
        "total": job.total,
        "done": job.done,
        "failed": job.failed,
        "current": None,
        "elapsed_seconds": elapsed_seconds,
        "tracks_per_min": tracks_per_min,
        "recent_tracks_per_min": recent_tracks_per_min,
        "recent_rate_window_seconds": recent_window_seconds,
        "recent_rate_completed": recent_completed,
        "eta_seconds": eta_seconds,
        "error_detail": None,
        "started_at": started_at,
        "finished_at": finished_at,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "finished_at_iso": job.finished_at,
        "model": job.model_name,
        "queued": job.queued,
        "leased": job.leased,
        "final_failed": job.final_failed,
        "status_breakdown": task_summary.get("status_breakdown", []),
        "oldest_lease": oldest_lease,
        "oldest_lease_age": oldest_lease_age,
        "completed": completed,
        "status_hint": status_hint,
        "supporting_workers": supporting_workers,
        "online_workers": online_workers,
        "leased_workers": task_summary.get("leased_workers", []),
        "recent_errors": recent_errors,
        "last_error": recent_errors[0] if recent_errors else None,
    }


def analysis_worker_dict(worker) -> dict[str, object]:
    age_seconds = analysis_worker_age_seconds(worker)
    connected = analysis_worker_connected(worker)
    return {
        "worker_id": worker.worker_id,
        "models": [model for model in worker.models.split(",") if model],
        "status": worker.status,
        "display_status": worker.status if connected else "stale",
        "connected": connected,
        "stale_seconds": age_seconds,
        "connected_ttl_seconds": WORKER_CONNECTED_TTL_SECONDS,
        "stage": worker.stage,
        "message": worker.message,
        "current_task_id": worker.current_task_id,
        "last_seen_at": worker.last_seen_at,
        "created_at": worker.created_at,
        "claimed_count": worker.claimed_count,
        "completed_count": worker.completed_count,
        "failed_count": worker.failed_count,
        "released_count": worker.released_count,
    }


def decode_worker_vector(item: WorkerResultItem) -> np.ndarray:
    if item.dtype != "float32":
        raise ValueError("Only float32 worker vectors are supported")
    try:
        raw = base64.b64decode(item.vector_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Invalid base64 vector") from exc
    expected = item.dim * np.dtype(np.float32).itemsize
    if len(raw) != expected:
        raise ValueError(f"Vector byte length mismatch: expected {expected}, got {len(raw)}")
    vector = np.frombuffer(raw, dtype=np.float32).copy()
    if not np.all(np.isfinite(vector)):
        raise ValueError("Vector contains NaN or Inf")
    norm = float(np.linalg.norm(vector))
    if norm <= 0.0:
        raise ValueError("Vector has zero norm")
    return vector


def decode_worker_scores(item: WorkerHeadOutputItem) -> np.ndarray:
    if item.dtype != "float32":
        raise ValueError("Only float32 worker scores are supported")
    try:
        raw = base64.b64decode(item.scores_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Invalid base64 scores") from exc
    expected = item.dim * np.dtype(np.float32).itemsize
    if len(raw) != expected:
        raise ValueError(f"Scores byte length mismatch: expected {expected}, got {len(raw)}")
    scores = np.frombuffer(raw, dtype=np.float32).copy()
    if not np.all(np.isfinite(scores)):
        raise ValueError("Scores contain NaN or Inf")
    return scores


def validate_worker_task_identity(
    store: Store,
    task_id: str,
    worker_id: str,
    track_id: int,
    model_name: str,
    file_size: int,
    mtime: int,
    *,
    expire_leases: bool = True,
) -> AnalysisTask:
    if expire_leases:
        store.expire_analysis_leases()
    task = store.get_analysis_task(task_id)
    if task is None:
        raise ValueError("Task not found")
    if task.status != "leased":
        raise ValueError(f"Task is not active: {task.status}")
    if task.lease_owner != worker_id:
        raise ValueError("Task is not leased by this worker")
    if task.track_id != track_id or task.model_name != model_name:
        raise ValueError("Task result identity mismatch")
    if task.file_size != file_size or task.mtime != mtime:
        raise ValueError("Task result is stale")
    track = store.get_track(task.track_id)
    if track is None:
        raise ValueError("Track not found")
    if track.file_size != task.file_size or track.mtime != task.mtime:
        raise ValueError("Track changed after task was created")
    return task


@app.get("/workers/tasks/{task_id}/state")
def get_worker_task_state(task_id: str, worker_id: str) -> dict[str, object]:
    store, _settings = context()
    task = store.get_analysis_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    job_status = store.get_analysis_job_status(task.job_id)
    active = (
        task.status == "leased"
        and task.lease_owner == worker_id
        and job_status == "running"
    )
    return {
        "task_id": task.id,
        "job_id": task.job_id,
        "status": task.status,
        "stage": task.stage,
        "lease_owner": task.lease_owner,
        "job_status": job_status,
        "active": active,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/search")
def api_v1_search(
    q: str = "",
    type: str = Query(default="all", pattern="^(all|artist|release|track)$"),
    limit: int = Query(default=8, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    store, _settings = context()
    query = " ".join(q.strip().split())
    results = store.search_entities(query, entity_type=type, limit=limit, offset=offset)
    artist_rows = results["artists"]["items"]
    release_rows = results["releases"]["items"]
    track_rows = results["tracks"]["items"]
    artists = [artist_summary_dict(row) for row in artist_rows]
    releases = [release_summary_dict(row) for row in release_rows]
    artists_by_track = store.artists_for_tracks([track.id for track in track_rows])
    tracks = [
        track_summary_dict(store, track, artists_by_track.get(track.id, []))
        for track in track_rows
    ]
    groups = [
        search_group("artists", "Artists", artists, int(results["artists"]["total"]), limit, offset),
        search_group("tracks", "Tracks", tracks, int(results["tracks"]["total"]), limit, offset),
        search_group("releases", "Releases", releases, int(results["releases"]["total"]), limit, offset),
    ]
    top_result = None
    for result_type, items in (("artist", artists), ("track", tracks), ("release", releases)):
        if items:
            top_result = {"entity_type": result_type, "entity": items[0]}
            break
    return {"query": query, "top_result": top_result, "groups": groups}


@app.get("/api/v1/artists/{artist_id}", response_model=None)
def api_v1_artist(artist_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    artist = store.get_artist(artist_id)
    if artist is None:
        return api_error(404, "not_found", "Artist not found")
    return {
        "artist": {**artist_summary_dict(artist), "sort_name": artist.artist.sort_name},
        "actions": [entity_action("mix", True, None)],
        "links": {
            "discography": f"/api/v1/artists/{artist_id}/discography",
            "top_tracks": f"/api/v1/artists/{artist_id}/top-tracks",
            "similar": f"/api/v1/artists/{artist_id}/similar",
        },
    }


@app.get("/api/v1/artists/{artist_id}/discography", response_model=None)
def api_v1_artist_discography(
    artist_id: int,
    sort: str = Query(default="release_date_desc", pattern="^(release_date_desc|release_date_asc|title)$"),
    limit: int | None = Query(default=None, ge=1, le=100),
    include_tracks: bool = False,
) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    artist = store.get_artist(artist_id)
    if artist is None:
        return api_error(404, "not_found", "Artist not found")
    titles = {
        "albums": "Albums",
        "eps": "EPs",
        "singles": "Singles",
        "compilations": "Compilations",
        "featured_in": "Featured In",
        "releases": "Releases",
    }
    discography = store.artist_discography(artist_id)
    groups = []
    for key, title in titles.items():
        items = discography[key]
        if sort == "title":
            items = sorted(items, key=lambda item: item.release.title.casefold())
        elif sort == "release_date_asc":
            items = sorted(
                items,
                key=lambda item: (
                    item.release.release_year is None,
                    item.release.release_year or 0,
                    item.release.title.casefold(),
                ),
            )
        if limit is not None:
            items = items[:limit]
        groups.append({"key": key, "title": title, "items": [release_summary_dict(item) for item in items]})
    return {"artist": artist_link_dict(artist.artist), "groups": groups}


@app.get("/api/v1/artists/{artist_id}/top-tracks", response_model=None)
def api_v1_artist_top_tracks(artist_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    artist = store.get_artist(artist_id)
    if artist is None:
        return api_error(404, "not_found", "Artist not found")
    return {"artist": artist_link_dict(artist.artist), "items": [], "basis": "local_playback", "available": False}


@app.get("/api/v1/artists/{artist_id}/similar", response_model=None)
def api_v1_artist_similar(artist_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    artist = store.get_artist(artist_id)
    if artist is None:
        return api_error(404, "not_found", "Artist not found")
    return {"artist": artist_link_dict(artist.artist), "items": [], "available": False, "basis": "not_available"}


@app.get("/api/v1/releases/{release_id}", response_model=None)
def api_v1_release(release_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    release = store.get_release(release_id)
    if release is None:
        return api_error(404, "not_found", "Release not found")
    return {
        "release": release_summary_dict(release),
        "actions": [entity_action("play", True, None), entity_action("shuffle", True, None)],
        "links": {
            "tracks": f"/api/v1/releases/{release_id}/tracks",
            "discography": f"/api/v1/releases/{release_id}/related-discography",
            "recommendations": f"/api/v1/releases/{release_id}/recommendations",
        },
    }


@app.get("/api/v1/releases/{release_id}/tracks", response_model=None)
def api_v1_release_tracks(release_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    release = store.get_release(release_id)
    if release is None:
        return api_error(404, "not_found", "Release not found")
    return {
        "release": {"id": release.release.id, "title": release.release.title},
        "items": [release_track_dict(store, item) for item in store.list_release_tracks(release_id)],
    }


@app.get("/api/v1/releases/{release_id}/related-discography", response_model=None)
def api_v1_release_related_discography(release_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    release = store.get_release(release_id)
    if release is None:
        return api_error(404, "not_found", "Release not found")
    items = store.related_discography_for_release(release_id)
    return {
        "release": {"id": release.release.id, "title": release.release.title},
        "context_artists": [artist_link_dict(artist) for artist in release.artists],
        "items": [release_summary_dict(item) for item in items],
    }


@app.get("/api/v1/releases/{release_id}/recommendations", response_model=None)
def api_v1_release_recommendations(release_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    release = store.get_release(release_id)
    if release is None:
        return api_error(404, "not_found", "Release not found")
    return {
        "release": {"id": release.release.id, "title": release.release.title},
        "available": False,
        "basis": "not_available",
        "items": [],
    }


@app.get("/api/v1/releases/{release_id}/cover", response_model=None)
def api_v1_release_cover(
    release_id: int,
    size: int = Query(default=300, ge=32, le=1000),
) -> Response | JSONResponse:
    store, settings = context()
    release = store.get_release(release_id)
    if release is None:
        return api_error(404, "not_found", "Release not found")
    if not release.release.cover_art_id:
        return api_error(404, "not_found", "Release has no cover art")
    try:
        cover = NavidromeClient(settings.navidrome).get_cover_art(release.release.cover_art_id, size=size)
    except Exception as exc:
        logger.warning("Release cover lookup failed release_id=%s: %s", release_id, exc)
        return api_error(404, "not_found", "Release cover not available")
    return Response(content=cover.payload, media_type=cover.content_type)


def run_maintenance_tick(store: Store | None = None) -> None:
    if store is None:
        store, _settings = context()
    store.expire_analysis_leases()
    store.refresh_active_analysis_jobs()
    sync_memory_jobs_from_durable_jobs(store.recent_analysis_jobs(limit=100))
    maybe_start_next_deferred_job()


def maintenance_loop() -> None:
    while not MAINTENANCE_STOP.wait(15):
        if SHUTDOWN_REQUESTED:
            return
        try:
            run_maintenance_tick()
        except Exception:
            logger.exception("Background maintenance tick failed")


@app.on_event("startup")
def start_maintenance_loop() -> None:
    MAINTENANCE_STOP.clear()
    Thread(target=maintenance_loop, name="discocs-maintenance", daemon=True).start()


@app.on_event("shutdown")
def shutdown_analyze_workers() -> None:
    global SHUTDOWN_REQUESTED
    logger.info("Application shutdown requested; terminating analyze workers")
    SHUTDOWN_REQUESTED = True
    MAINTENANCE_STOP.set()
    with ANALYZE_EXECUTORS_LOCK:
        executors = list(ANALYZE_EXECUTORS)
    for executor in executors:
        terminate_process_pool(executor)


@app.get("/metrics/features")
def metrics_features(
    source: str = Query(default="audio_features", pattern="^(audio_features|heads)$"),
    extractor: str = AUDIO_FEATURE_EXTRACTOR,
):
    store, _settings = context()
    if source == "heads":
        summaries = store.list_head_summaries()
        return {
            "source": source,
            "features": [
                {
                    "name": item.model_name,
                    "extractor": "discogs_effnet_heads",
                    "value_count": item.prediction_track_count,
                    "text_count": item.label_count,
                    "track_count": item.output_count,
                    "min_value": None,
                    "max_value": item.max_score,
                    "avg_value": item.avg_score,
                    "unit": "score",
                }
                for item in summaries
            ],
        }
    summaries = store.list_feature_summaries(extractor or None)
    return {
        "source": source,
        "extractor": extractor,
        "features": [
            {
                "name": item.name,
                "extractor": item.extractor,
                "value_count": item.value_count,
                "text_count": item.text_count,
                "track_count": item.track_count,
                "min_value": item.min_value,
                "max_value": item.max_value,
                "avg_value": item.avg_value,
                "unit": item.unit,
            }
            for item in summaries
        ],
    }


@app.get("/metrics/features/{feature_name}/values")
def metrics_feature_values(
    feature_name: str,
    source: str = Query(default="audio_features", pattern="^(audio_features|heads)$"),
    extractor: str = AUDIO_FEATURE_EXTRACTOR,
    limit: int = Query(default=100, ge=1, le=500),
):
    store, _settings = context()
    if source == "heads":
        values = store.list_head_prediction_labels(feature_name, limit)
        return {
            "feature": feature_name,
            "source": source,
            "values": [
                {
                    "value": label,
                    "track_count": track_count,
                    "avg_score": avg_score,
                    "max_score": max_score,
                }
                for label, track_count, avg_score, max_score in values
            ],
        }
    values = store.list_feature_text_values(feature_name, extractor or None, limit)
    return {
        "feature": feature_name,
        "source": source,
        "extractor": extractor,
        "values": [
            {"value": value, "track_count": track_count}
            for value, track_count in values
        ],
    }


@app.post("/metrics/search")
def metrics_search(request: FeatureSearchRequest):
    store, _settings = context()
    filters = [
        FeatureFilter(
            name=item.name,
            min_value=item.min_value,
            max_value=item.max_value,
            text_values=tuple(value for value in item.text_values if value),
        )
        for item in request.filters
        if item.name.strip()
    ]
    if request.source == "heads":
        results = store.search_tracks_by_head_predictions(
            filters,
            query=request.query,
            sort_by=request.sort_by,
            sort_direction=request.sort_direction,
            limit=request.limit,
        )
    else:
        results = store.search_tracks_by_features(
            filters,
            query=request.query,
            extractor=request.extractor or None,
            sort_by=request.sort_by,
            sort_direction=request.sort_direction,
            limit=request.limit,
        )
    return {
        "source": request.source,
        "extractor": request.extractor,
        "count": len(results),
        "results": [enriched_feature_track_dict(store, item) for item in results],
    }


@app.get("/", response_class=HTMLResponse)
def test_ui() -> HTMLResponse:
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


@app.get("/stats")
def stats(model: str = "discogs_multi") -> dict[str, object]:
    cached = cached_stats(model)
    if cached is not None:
        return cached
    started = perf_counter()
    store, settings = context()
    logger.info("Stats build started model=%s", model)
    head_started = perf_counter()
    head_status = head_pack_status(store, settings)
    logger.info("Stats head_pack_status seconds=%.3f", perf_counter() - head_started)
    audio_started = perf_counter()
    audio_status = audio_feature_status(store)
    logger.info("Stats audio_feature_status seconds=%.3f", perf_counter() - audio_started)
    counts_started = perf_counter()
    tracks = store.count_tracks()
    missing_files = store.count_missing_files()
    navidrome_external_tracks = store.count_external_tracks("navidrome")
    errored_files = analysis_error_count(store)
    embeddings = store.count_embeddings(model)
    missing_embeddings = store.count_missing_embeddings(model)
    index_status = recommender_index_status(settings, model, embeddings)
    known_models = sorted(model for model in [*MODEL_FILES, MUQ_MULAN_MODEL] if model != DISCOGS_EFFNET_MODEL)
    model_stats = []
    for known_model in known_models:
        model_embeddings = store.count_embeddings(known_model)
        model_missing_embeddings = store.count_missing_embeddings(known_model)
        model_index_status = recommender_index_status(settings, known_model, model_embeddings)
        if known_model == MUQ_MULAN_MODEL:
            known_model_path = settings.model_dir / "muq"
            known_model_exists = known_model_path.exists()
        else:
            known_model_path = settings.model_path(known_model)
            known_model_exists = known_model_path.exists()
        model_stats.append(
            {
                "model": known_model,
                "embeddings": model_embeddings,
                "missing_embeddings": model_missing_embeddings,
                "model_path": str(known_model_path),
                "model_exists": known_model_exists,
                "index": str(settings.index_path(known_model)),
                "index_exists": settings.index_path(known_model).exists(),
                "index_status": model_index_status["status"],
                "index_stale": model_index_status["stale"],
                "index_count": model_index_status["count"],
                "index_embedding_count": model_index_status["embedding_count"],
                "index_metadata_exists": model_index_status["metadata_exists"],
                "index_metadata": model_index_status["metadata_path"],
            }
        )
    if model == MUQ_MULAN_MODEL:
        model_path = settings.model_dir / "muq"
        model_exists = model_path.exists()
    else:
        model_path = settings.model_path(model)
        model_exists = model_path.exists()
    logger.info("Stats base_counts seconds=%.3f", perf_counter() - counts_started)
    data: dict[str, object] = {
        "db": str(settings.db_path),
        "tracks": tracks,
        "missing_files": missing_files,
        "analysis_error_count": errored_files,
        "navidrome_external_tracks": navidrome_external_tracks,
        "embeddings": embeddings,
        "missing_embeddings": missing_embeddings,
        "head_pack_expected_outputs": head_status["expected_outputs"],
        "head_pack_outputs": head_status["saved_outputs"],
        "head_pack_complete_tracks": head_status["complete_tracks"],
        "head_pack_missing_tracks": head_status["missing_tracks"],
        "missing_head_pack_tracks": head_status["missing_tracks"],
        "head_pack": head_status,
        "audio_features_complete_tracks": audio_status["complete_tracks"],
        "audio_features_missing_tracks": audio_status["missing_tracks"],
        "audio_features": audio_status,
        "model": model,
        "models": known_models,
        "model_stats": model_stats,
        "model_path": str(model_path),
        "model_exists": model_exists,
        "index": str(settings.index_path(model)),
        "index_exists": settings.index_path(model).exists(),
        "index_status": index_status["status"],
        "index_stale": index_status["stale"],
        "index_count": index_status["count"],
        "index_embedding_count": index_status["embedding_count"],
        "index_metadata_exists": index_status["metadata_exists"],
        "index_metadata": index_status["metadata_path"],
    }
    remember_stats(model, data)
    logger.info("Stats build completed model=%s seconds=%.3f", model, perf_counter() - started)
    return data


def cached_stats(model: str) -> dict[str, object] | None:
    now = time.time()
    with STATS_CACHE_LOCK:
        cached = STATS_CACHE.get(model)
        if cached is None:
            return None
        cached_at, data = cached
        if now - cached_at > STATS_CACHE_TTL_SECONDS:
            STATS_CACHE.pop(model, None)
            return None
        result = dict(data)
        result["cached"] = True
        return result


def remember_stats(model: str, data: dict[str, object]) -> None:
    with STATS_CACHE_LOCK:
        STATS_CACHE[model] = (time.time(), dict(data))


def clear_stats_cache() -> None:
    with STATS_CACHE_LOCK:
        STATS_CACHE.clear()


@app.get("/settings/navidrome")
def get_navidrome_settings() -> dict[str, object]:
    _store, settings = context()
    nav = settings.navidrome
    return {
        "url": nav.url,
        "user": nav.user,
        "password_set": bool(nav.password),
        "auth_mode": nav.auth_mode,
        "timeout_seconds": nav.timeout_seconds,
        "download_mode": nav.download_mode,
        "temp_dir": str(nav.temp_dir),
    }


@app.put("/settings/navidrome")
def update_navidrome_settings(request: NavidromeSettingsRequest) -> dict[str, object]:
    _store, settings = context()
    saved = load_runtime_settings(settings.data_dir)
    existing = saved.get("navidrome", {})
    existing_password = existing.get("password", "") if isinstance(existing, dict) else ""
    password = request.password if request.password is not None else str(existing_password)
    saved["navidrome"] = {
        "url": request.url.strip(),
        "user": request.user.strip(),
        "password": password,
        "auth_mode": request.auth_mode,
        "timeout_seconds": request.timeout_seconds,
        "download_mode": request.download_mode,
        "temp_dir": request.temp_dir.strip()
        if request.temp_dir
        else str(settings.data_dir / "tmp" / "navidrome"),
    }
    save_runtime_settings(settings.data_dir, saved)
    navidrome_logger.info(
        "Saved Navidrome settings url=%s user=%s auth_mode=%s timeout_seconds=%s download_mode=%s password_set=%s",
        request.url,
        request.user,
        request.auth_mode,
        request.timeout_seconds,
        request.download_mode,
        bool(password),
    )
    return get_navidrome_settings()


@app.post("/navidrome/ping")
def ping_navidrome() -> dict[str, object]:
    _store, settings = context()
    try:
        payload = NavidromeClient(settings.navidrome).ping()
    except Exception as exc:
        navidrome_logger.warning("Navidrome ping failed", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Navidrome ping failed: {exc}") from exc
    return {
        "status": "ok",
        "version": payload.get("version", ""),
        "server_version": payload.get("serverVersion", ""),
    }


@app.post("/navidrome/plugin-event")
def record_navidrome_plugin_event(request: NavidromePluginEventRequest) -> dict[str, str]:
    navidrome_plugin_logger.info(
        "plugin_event event=%s item_id=%s model=%s count=%s status=%s discocs_url=%s message=%s",
        request.event,
        request.item_id,
        request.model,
        request.count,
        request.status,
        request.discocs_url,
        request.message,
    )
    return {"status": "ok"}


@app.get("/instant-mix/settings")
def get_instant_mix_settings() -> dict[str, object]:
    _store, settings = context()
    return instant_mix_settings(settings)


@app.put("/instant-mix/settings")
def update_instant_mix_settings(request: InstantMixSettingsRequest) -> dict[str, object]:
    _store, settings = context()
    saved = load_runtime_settings(settings.data_dir)
    saved["instant_mix"] = {
        "model": request.model,
        "count": request.count,
        "min_similarity": request.min_similarity,
        "max_per_artist": request.max_per_artist,
        "exclude_same_album": request.exclude_same_album,
        "count_collaboration_artists": request.count_collaboration_artists,
    }
    save_runtime_settings(settings.data_dir, saved)
    return instant_mix_settings(settings)


@app.get("/instant-mix/requests")
def list_instant_mix_requests(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    store, _settings = context()
    try:
        requests = store.list_instant_mix_requests(limit=limit, offset=offset)
    except sqlite3.DatabaseError as exc:
        logger.exception("Failed to list instant mix requests")
        raise HTTPException(
            status_code=503,
            detail=f"Instant mix history could not be read from SQLite: {exc}",
        ) from exc
    return {
        "count": len(requests),
        "limit": limit,
        "offset": offset,
        "results": [
            instant_mix_request_dict(request, include_results=False, store=store)
            for request in requests
        ],
    }


@app.get("/instant-mix/requests/{request_id}")
def get_instant_mix_request(request_id: str) -> dict[str, object]:
    store, _settings = context()
    try:
        request = store.get_instant_mix_request(request_id)
    except sqlite3.DatabaseError as exc:
        logger.exception("Failed to read instant mix request request_id=%s", request_id)
        raise HTTPException(
            status_code=503,
            detail=f"Instant mix request could not be read from SQLite: {exc}",
        ) from exc
    if request is None:
        raise HTTPException(status_code=404, detail="Instant mix request not found")
    return instant_mix_request_dict(request, include_results=True, store=store)


@app.get("/models/head-pack")
def get_head_pack() -> dict[str, object]:
    store, settings = context()
    return head_pack_status(store, settings)


@app.get("/tracks")
def list_tracks(
    query: str = "",
    limit: int = Query(50, ge=1, le=500),
    embedding_status: str = Query("all", pattern="^(all|ready|missing)$"),
    model: str = "discogs_multi",
    folder: str | None = None,
    genre: str | None = None,
    year: int | None = None,
    artist: str | None = None,
    album: str | None = None,
) -> dict[str, object]:
    store, _settings = context()
    try:
        tracks = store.list_tracks(
            query=query,
            limit=limit,
            model_name=model,
            embedding_status=embedding_status,
            folder=folder,
            genre=genre,
            year=year,
            artist=artist,
            album=album,
        )
    except ValueError as exc:
        logger.warning("Invalid track list filters query=%s embedding_status=%s model=%s error=%s", query, embedding_status, model, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"results": [enriched_track_listing_dict(store, track) for track in tracks]}


@app.get("/tracks/search")
def search_tracks(
    q: str = "",
    limit: int = Query(50, ge=1, le=500),
    embedding_status: str = Query("all", pattern="^(all|ready|missing)$"),
    model: str = "discogs_multi",
) -> dict[str, object]:
    return list_tracks(
        query=q,
        limit=limit,
        embedding_status=embedding_status,
        model=model,
    )


@app.get("/lost-files")
def list_lost_files(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
) -> dict[str, object]:
    store, _settings = context()
    count = store.count_missing_files()
    offset = (page - 1) * page_size
    tracks = store.list_missing_tracks(limit=page_size, offset=offset)
    return {
        "count": count,
        "page": page,
        "page_size": page_size,
        "pages": max((count + page_size - 1) // page_size, 1),
        "results": [track_dict(track) for track in tracks],
    }


@app.get("/analysis/errors")
def list_analysis_errors(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
) -> dict[str, object]:
    store, _settings = context()
    offset = (page - 1) * page_size
    where = """
        t.error IS NOT NULL
        AND COALESCE(t.error_type, '') != 'Cancelled'
        AND COALESCE(t.stage, '') != 'cancelled'
        AND t.error NOT LIKE 'Model file not found:%'
    """
    with store.connect() as conn:
        count = conn.execute(
            f"""
            SELECT COUNT(*) FROM (
                SELECT t.track_id, t.model_name
                FROM analysis_tasks t
                JOIN tracks tr ON tr.id = t.track_id
                WHERE {where}
                    GROUP BY t.track_id, t.model_name
            )
            """
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            WITH latest AS (
                SELECT t.track_id, t.model_name, MAX(t.updated_at) AS updated_at
                FROM analysis_tasks t
                JOIN tracks tr ON tr.id = t.track_id
                WHERE {where}
                GROUP BY t.track_id, t.model_name
            )
            SELECT
                t.id AS task_id,
                t.job_id,
                j.kind AS job_kind,
                t.track_id,
                t.model_name,
                t.status,
                t.attempts,
                t.max_attempts,
                t.path,
                t.file_size,
                t.mtime,
                t.error,
                t.error_type,
                t.stage,
                t.updated_at,
                t.completed_at,
                tr.artist,
                tr.title,
                tr.album
            FROM analysis_tasks t
            JOIN latest l
                ON l.track_id = t.track_id
                AND l.model_name = t.model_name
                AND l.updated_at = t.updated_at
            JOIN analysis_jobs j ON j.id = t.job_id
            JOIN tracks tr ON tr.id = t.track_id
            WHERE {where}
            ORDER BY t.updated_at DESC, t.id DESC
            LIMIT ? OFFSET ?
            """,
            (page_size, offset),
        ).fetchall()
    return {
        "count": count,
        "page": page,
        "page_size": page_size,
        "pages": max((count + page_size - 1) // page_size, 1),
        "results": [dict(row) for row in rows],
    }


@app.delete("/analysis/errors")
def delete_analysis_errors(request: DeleteAnalysisErrorsRequest) -> dict[str, object]:
    store, _settings = context()
    where = """
        error IS NOT NULL
        AND COALESCE(error_type, '') != 'Cancelled'
        AND COALESCE(stage, '') != 'cancelled'
        AND error NOT LIKE 'Model file not found:%'
    """
    with store.connect() as conn:
        if request.all_errors:
            cursor = conn.execute(
                f"""
                UPDATE analysis_tasks
                SET error = NULL,
                    error_type = NULL,
                    stage = NULL,
                    updated_at = ?
                WHERE id IN (
                    SELECT t.id
                    FROM analysis_tasks t
                    JOIN tracks tr ON tr.id = t.track_id
                    WHERE {where}
                )
                """,
                (datetime.now().isoformat(),),
            )
            return {"status": "ok", "cleared": cursor.rowcount}
        task_ids = [task_id for task_id in request.task_ids if task_id]
        if not task_ids:
            return {"status": "ok", "cleared": 0}
        placeholders = ",".join("?" for _ in task_ids)
        cursor = conn.execute(
            f"""
            UPDATE analysis_tasks
            SET error = NULL,
                error_type = NULL,
                stage = NULL,
                updated_at = ?
            WHERE id IN ({placeholders})
            """,
            (datetime.now().isoformat(), *task_ids),
        )
    return {"status": "ok", "cleared": cursor.rowcount}


@app.delete("/lost-files")
def delete_lost_files(request: DeleteTracksRequest) -> dict[str, object]:
    store, _settings = context()
    if request.all_missing:
        deleted = store.delete_missing_tracks()
        logger.info("Deleted all lost file records deleted=%s", deleted)
        return {"status": "ok", "deleted": deleted}
    missing_ids = {track.id for track in store.list_missing_tracks(limit=100000)}
    track_ids = [track_id for track_id in request.track_ids if track_id in missing_ids]
    deleted = store.delete_tracks(track_ids)
    logger.info("Deleted lost file records requested=%s deleted=%s", len(request.track_ids), deleted)
    return {"status": "ok", "deleted": deleted}


@app.get("/browse/facets")
def browse_facets(
    model: str = "discogs_multi",
    embedding_status: str = Query("all", pattern="^(all|ready|missing)$"),
) -> dict[str, object]:
    store, _settings = context()
    try:
        return store.browser_facets(model_name=model, embedding_status=embedding_status)
    except ValueError as exc:
        logger.warning("Invalid browse facets filters model=%s embedding_status=%s error=%s", model, embedding_status, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/tracks/{track_id}")
def get_track(track_id: int) -> dict[str, object]:
    store, _settings = context()
    track = store.get_track(track_id)
    if track is None:
        logger.warning("Track not found track_id=%s", track_id)
        raise HTTPException(status_code=404, detail="Track not found")
    return enriched_track_dict(store, track)


@app.get("/tracks/{track_id}/analysis")
def get_track_analysis(track_id: int) -> dict[str, object]:
    store, _settings = context()
    track = store.get_track(track_id)
    if track is None:
        logger.warning("Track analysis requested for missing track track_id=%s", track_id)
        raise HTTPException(status_code=404, detail="Track not found")
    predictions_by_model = store.list_predictions(track_id)
    outputs = [
        {
            "model_name": output.model_name,
            "dim": int(output.scores.shape[0]),
            "dtype": output.dtype,
            "aggregation": output.aggregation,
            "scores": [float(score) for score in output.scores],
            "top_predictions": [
                prediction_dict(prediction)
                for prediction in predictions_by_model.get(output.model_name, [])
            ],
        }
        for output in store.list_model_outputs(track_id)
    ]
    output_names = {output["model_name"] for output in outputs}
    for model_name, predictions in predictions_by_model.items():
        if model_name not in output_names:
            outputs.append(
                {
                    "model_name": model_name,
                    "dim": 0,
                    "dtype": None,
                    "aggregation": None,
                    "scores": [],
                    "top_predictions": [prediction_dict(prediction) for prediction in predictions],
                }
            )
    outputs.sort(key=lambda output: str(output["model_name"]))
    return {
        "track": enriched_track_dict(store, track),
        "outputs": outputs,
        "features": [feature_dict(feature) for feature in store.load_features(track_id)],
    }


@app.get("/tracks/{track_id}/audio")
def get_track_audio(track_id: int) -> FileResponse:
    store, settings = context()
    track = store.get_track(track_id)
    if track is None:
        logger.warning("Audio requested for missing track track_id=%s", track_id)
        raise HTTPException(status_code=404, detail="Track not found")
    if is_navidrome_track(track):
        try:
            manager = track_audio_path(store, settings, track)
            path = manager.__enter__()
        except Exception as exc:
            logger.warning(
                "Navidrome audio unavailable track_id=%s path=%s",
                track_id,
                track.path,
                exc_info=True,
            )
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        store.mark_track_available(track_id)
        return FileResponse(path, background=BackgroundTask(manager.__exit__, None, None, None))
    path = Path(track.path)
    if not path.exists() or not path.is_file():
        logger.warning("Audio file missing track_id=%s path=%s", track_id, path)
        store.mark_track_missing(track_id)
        raise HTTPException(status_code=410, detail="Audio file not mounted or no longer exists")
    store.mark_track_available(track_id)
    return FileResponse(path)


def cached_cover_response(cache_key: tuple[str, int]) -> tuple[bytes, str] | None:
    now = time.time()
    with COVER_CACHE_LOCK:
        cached = COVER_CACHE.get(cache_key)
        if cached is None:
            return None
        cached_at, payload, content_type = cached
        if now - cached_at > COVER_CACHE_TTL_SECONDS:
            COVER_CACHE.pop(cache_key, None)
            return None
        return payload, content_type


def cached_cover_error(cache_key: tuple[str, int]) -> str | None:
    now = time.time()
    with COVER_CACHE_LOCK:
        cached = COVER_ERROR_CACHE.get(cache_key)
        if cached is None:
            return None
        cached_at, message = cached
        if now - cached_at > COVER_ERROR_CACHE_TTL_SECONDS:
            COVER_ERROR_CACHE.pop(cache_key, None)
            return None
        return message


def remember_cover(cache_key: tuple[str, int], payload: bytes, content_type: str) -> None:
    with COVER_CACHE_LOCK:
        COVER_CACHE[cache_key] = (time.time(), payload, content_type)
        COVER_ERROR_CACHE.pop(cache_key, None)
        while len(COVER_CACHE) > COVER_CACHE_MAX_ITEMS:
            oldest_key = next(iter(COVER_CACHE))
            COVER_CACHE.pop(oldest_key, None)


def remember_cover_error(cache_key: tuple[str, int], message: str) -> None:
    with COVER_CACHE_LOCK:
        COVER_ERROR_CACHE[cache_key] = (time.time(), message)
        while len(COVER_ERROR_CACHE) > COVER_CACHE_MAX_ITEMS:
            oldest_key = next(iter(COVER_ERROR_CACHE))
            COVER_ERROR_CACHE.pop(oldest_key, None)


def cover_response(payload: bytes, content_type: str) -> Response:
    return Response(
        content=payload,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=86400"},
    )


@app.get("/tracks/{track_id}/cover")
def get_track_cover(track_id: int, size: int = Query(default=96, ge=32, le=600)) -> Response:
    started = perf_counter()
    store, settings = context()
    track = store.get_track(track_id)
    if track is None:
        logger.warning("Cover requested for missing track track_id=%s", track_id)
        raise HTTPException(status_code=404, detail="Track not found")
    external_id = store.external_id_for_track("navidrome", track_id)
    if external_id is None:
        raise HTTPException(status_code=404, detail="Track has no Navidrome mapping")
    mapping = store.get_external_track("navidrome", external_id)
    raw: dict[str, object] = {}
    if mapping is not None and mapping.raw_json:
        try:
            parsed = json.loads(mapping.raw_json)
            raw = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            logger.warning("Invalid Navidrome raw_json for track_id=%s item_id=%s", track_id, external_id)
    cover_art_id = raw.get("coverArt")
    if not cover_art_id:
        raise HTTPException(status_code=404, detail="Track has no Navidrome cover art id")
    cache_key = (str(cover_art_id), size)
    cached = cached_cover_response(cache_key)
    if cached is not None:
        payload, content_type = cached
        logger.info(
            "Cover cache hit track_id=%s item_id=%s cover_art_id=%s size=%s seconds=%.3f",
            track_id,
            external_id,
            cover_art_id,
            size,
            perf_counter() - started,
        )
        return cover_response(payload, content_type)
    cached_error = cached_cover_error(cache_key)
    if cached_error is not None:
        logger.info(
            "Cover negative cache hit track_id=%s item_id=%s cover_art_id=%s size=%s error=%s seconds=%.3f",
            track_id,
            external_id,
            cover_art_id,
            size,
            cached_error,
            perf_counter() - started,
        )
        raise HTTPException(status_code=502, detail=cached_error)
    logger.info(
        "Cover fetch started track_id=%s item_id=%s cover_art_id=%s size=%s timeout_seconds=%s",
        track_id,
        external_id,
        cover_art_id,
        size,
        min(settings.navidrome.timeout_seconds, COVER_TIMEOUT_SECONDS),
    )
    try:
        cover_settings = replace(
            settings.navidrome,
            timeout_seconds=min(settings.navidrome.timeout_seconds, COVER_TIMEOUT_SECONDS),
        )
        cover = NavidromeClient(cover_settings).get_cover_art(str(cover_art_id), size=size)
    except Exception as exc:
        remember_cover_error(cache_key, str(exc))
        navidrome_logger.warning(
            "Navidrome cover art unavailable track_id=%s item_id=%s cover_art_id=%s",
            track_id,
            external_id,
            cover_art_id,
            exc_info=True,
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    remember_cover(cache_key, cover.payload, cover.content_type)
    logger.info(
        "Cover fetch completed track_id=%s item_id=%s cover_art_id=%s size=%s bytes=%s seconds=%.3f",
        track_id,
        external_id,
        cover_art_id,
        size,
        len(cover.payload),
        perf_counter() - started,
    )
    return cover_response(cover.payload, cover.content_type)


@app.get("/tracks/{track_id}/similar")
def get_similar_tracks(
    track_id: int,
    model: str = "discogs_multi",
    k: int = 30,
    max_per_artist: int = 2,
    exclude_same_album: bool = True,
) -> dict[str, object]:
    store, settings = context()
    seed = store.get_track(track_id)
    if seed is None:
        logger.warning("Similar requested for missing track track_id=%s", track_id)
        raise HTTPException(status_code=404, detail="Track not found")
    try:
        results = Recommender(store, settings, model).similar(
            seed,
            k=k,
            max_per_artist=max_per_artist,
            exclude_same_album=exclude_same_album,
        )
        ratings = store.feedback_for_seed(seed.id, model)
        results = [
            replace(result, rating=ratings.get(result.track.id))
            for result in results
        ]
    except FileNotFoundError as exc:
        logger.warning("Similar index missing track_id=%s model=%s error=%s", track_id, model, exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LookupError as exc:
        logger.warning("Similar lookup failed track_id=%s model=%s error=%s", track_id, model, exc)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "seed": enriched_track_dict(store, seed),
        "model": model,
        "results": [enriched_similar_track_dict(store, result) for result in results],
    }


@app.post("/text-search")
def text_search(request: TextSearchRequest) -> dict[str, object]:
    query = request.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Text query is empty")
    store, settings = context()
    started = perf_counter()
    try:
        vector = text_search_embedder(settings).extract_text_vector(query)
        results = Recommender(store, settings, MUQ_MULAN_MODEL).similar_vector(
            vector,
            exclude_track_ids=set(),
            album_seeds=[],
            k=request.count,
            max_per_artist=request.max_per_artist,
            exclude_same_album=request.exclude_same_album,
            count_collaboration_artists=request.count_collaboration_artists,
        )
    except FileNotFoundError as exc:
        logger.warning("Text search index missing model=%s error=%s", MUQ_MULAN_MODEL, exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.warning("Text search embedding failed error=%s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    filtered = [
        result
        for result in results
        if request.min_similarity is None or result.similarity >= request.min_similarity
    ]
    similarities = [float(result.similarity) for result in filtered]
    logger.info(
        "Text search completed query=%r results=%s returned=%s seconds=%.3f",
        query,
        len(results),
        len(filtered),
        perf_counter() - started,
    )
    return {
        "query": query,
        "model": MUQ_MULAN_MODEL,
        "count": request.count,
        "min_similarity": request.min_similarity,
        "vector_norm": float(np.linalg.norm(vector)),
        "similarity_min": min(similarities) if similarities else None,
        "similarity_max": max(similarities) if similarities else None,
        "similarity_avg": (sum(similarities) / len(similarities)) if similarities else None,
        "results": [enriched_similar_track_dict(store, result) for result in filtered],
    }


def _parse_seed_ids_param(seed_ids: str) -> list[int]:
    parts = [part.strip() for part in seed_ids.split(",") if part.strip()]
    if not parts:
        raise HTTPException(status_code=400, detail="seed_ids is required")
    if len(parts) > MAX_MIX_SEEDS:
        raise HTTPException(
            status_code=400,
            detail=f"At most {MAX_MIX_SEEDS} seed_ids are allowed",
        )
    parsed: list[int] = []
    for part in parts:
        try:
            parsed.append(int(part))
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid seed id: {part}",
            ) from exc
    return parsed


@app.get("/tracks/similar/mix")
def get_similar_mix_tracks(
    seed_ids: str = Query(..., description="Comma-separated track ids"),
    model: str = "discogs_multi",
    k: int = 30,
    max_per_artist: int = 2,
    exclude_same_album: bool = True,
) -> dict[str, object]:
    store, settings = context()
    track_ids = _parse_seed_ids_param(seed_ids)
    seeds: list[Track] = []
    missing_ids: list[int] = []
    for track_id in track_ids:
        track = store.get_track(track_id)
        if track is None:
            missing_ids.append(track_id)
            continue
        seeds.append(track)
    if missing_ids:
        logger.warning("Similar mix requested missing tracks track_ids=%s", missing_ids)
        raise HTTPException(
            status_code=404,
            detail=f"Tracks not found: {', '.join(str(track_id) for track_id in missing_ids)}",
        )
    try:
        results, skipped_seed_ids = Recommender(store, settings, model).similar_mix(
            seeds,
            k=k,
            max_per_artist=max_per_artist,
            exclude_same_album=exclude_same_album,
        )
    except FileNotFoundError as exc:
        logger.warning("Similar mix index missing model=%s error=%s", model, exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LookupError as exc:
        logger.warning("Similar mix lookup failed model=%s error=%s", model, exc)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "blend": "average",
        "seeds": [enriched_track_dict(store, track) for track in seeds],
        "skipped_seed_ids": skipped_seed_ids,
        "model": model,
        "results": [enriched_similar_track_dict(store, result) for result in results],
    }


def _navidrome_client(settings: Settings) -> NavidromeClient:
    try:
        return NavidromeClient(settings.navidrome)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/navidrome/starred")
def get_navidrome_starred(model: str = "discogs_multi") -> dict[str, object]:
    store, settings = context()
    client = _navidrome_client(settings)
    try:
        return build_starred_catalog(
            store,
            client,
            model=model,
            user=settings.navidrome.user,
        )
    except Exception as exc:
        navidrome_logger.warning("Navidrome starred failed model=%s error=%s", model, exc)
        raise HTTPException(status_code=502, detail=f"Navidrome starred failed: {exc}") from exc


@app.get("/navidrome/starred/ids")
def get_navidrome_starred_ids() -> dict[str, object]:
    store, settings = context()
    client = _navidrome_client(settings)
    try:
        data = build_starred_track_ids(store, client, user=settings.navidrome.user)
        navidrome_logger.info(
            "Navidrome starred ids user=%s count=%s mapped_count=%s track_ids=%s",
            data.get("user"),
            data.get("count"),
            data.get("mapped_count"),
            data.get("track_ids"),
        )
        return data
    except Exception as exc:
        navidrome_logger.warning("Navidrome starred ids failed error=%s", exc)
        raise HTTPException(status_code=502, detail=f"Navidrome starred failed: {exc}") from exc


@app.put("/tracks/{track_id}/navidrome-star")
def set_track_navidrome_star(track_id: int, request: NavidromeStarRequest) -> dict[str, object]:
    store, settings = context()
    track = store.get_track(track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")
    item_id = store.external_id_for_track("navidrome", track_id)
    if item_id is None:
        raise HTTPException(status_code=404, detail="Track has no Navidrome mapping")
    client = _navidrome_client(settings)
    try:
        if request.starred:
            client.star_song(item_id)
        else:
            client.unstar_song(item_id)
    except Exception as exc:
        navidrome_logger.warning(
            "Navidrome star update failed track_id=%s item_id=%s starred=%s error=%s",
            track_id,
            item_id,
            request.starred,
            exc,
        )
        raise HTTPException(status_code=502, detail=f"Navidrome star update failed: {exc}") from exc
    navidrome_logger.info(
        "Navidrome star update ok user=%s track_id=%s item_id=%s starred=%s",
        settings.navidrome.user,
        track_id,
        item_id,
        request.starred,
    )
    return {
        "track_id": track_id,
        "item_id": item_id,
        "starred": request.starred,
        "user": settings.navidrome.user,
    }


@app.get("/navidrome/starred/similar")
def get_navidrome_starred_similar(
    model: str = "discogs_multi",
    count: int = Query(default=50, ge=1, le=500),
    max_per_artist: int = Query(default=2, ge=1, le=100),
    exclude_same_album: bool = True,
) -> dict[str, object]:
    store, settings = context()
    client = _navidrome_client(settings)
    try:
        catalog = build_starred_catalog(
            store,
            client,
            model=model,
            user=settings.navidrome.user,
        )
    except Exception as exc:
        navidrome_logger.warning("Navidrome starred similar catalog failed model=%s error=%s", model, exc)
        raise HTTPException(status_code=502, detail=f"Navidrome starred failed: {exc}") from exc

    ready_tracks = ready_tracks_from_starred_catalog(catalog, store, model)
    if not ready_tracks:
        raise HTTPException(
            status_code=404,
            detail=(
                "No ready liked tracks with embeddings. "
                "Sync Navidrome catalog and analyze missing tracks first."
            ),
        )
    try:
        results, skipped_seed_ids = Recommender(store, settings, model).similar_mix(
            ready_tracks,
            k=count,
            max_per_artist=max_per_artist,
            exclude_same_album=exclude_same_album,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "source": "navidrome_starred",
        "blend": "average",
        "user": catalog.get("user", ""),
        "count": catalog.get("count", 0),
        "mapped_count": catalog.get("mapped_count", 0),
        "ready_count": catalog.get("ready_count", 0),
        "missing_embedding_count": catalog.get("missing_embedding_count", 0),
        "not_synced_count": catalog.get("not_synced_count", 0),
        "skipped_seed_ids": skipped_seed_ids,
        "model": model,
        "results": [enriched_similar_track_dict(store, result) for result in results],
    }


@app.get("/navidrome/similar", response_model=NavidromeSimilarResponse)
def get_navidrome_similar(
    item_id: str,
    count: int = Query(default=50, ge=1, le=500),
    model: str | None = None,
    max_per_artist: int | None = Query(default=None, ge=1, le=100),
    exclude_same_album: bool | None = None,
) -> NavidromeSimilarResponse:
    started = perf_counter()
    request_id = str(uuid4())
    store, settings = context()
    mix_settings = instant_mix_settings(settings)
    requested_model = model
    model = str(mix_settings["model"])
    effective_count = int(mix_settings["count"])
    min_similarity = mix_settings["min_similarity"]
    effective_max_per_artist = int(mix_settings["max_per_artist"])
    effective_exclude_same_album = bool(mix_settings["exclude_same_album"])
    effective_count_collaboration_artists = bool(mix_settings["count_collaboration_artists"])
    navidrome_logger.info(
        "Navidrome similar request request_id=%s item_id=%s requested_model=%s model=%s requested_count=%s effective_count=%s max_per_artist=%s exclude_same_album=%s min_similarity=%s",
        request_id,
        item_id,
        requested_model,
        model,
        count,
        effective_count,
        effective_max_per_artist,
        effective_exclude_same_album,
        min_similarity,
    )
    navidrome_plugin_logger.info(
        "api_request request_id=%s item_id=%s requested_model=%s model=%s requested_count=%s effective_count=%s max_per_artist=%s exclude_same_album=%s min_similarity=%s",
        request_id,
        item_id,
        requested_model,
        model,
        count,
        effective_count,
        effective_max_per_artist,
        effective_exclude_same_album,
        min_similarity,
    )
    seed = store.get_track_by_external_id("navidrome", item_id)
    if seed is None:
        duration_ms = (perf_counter() - started) * 1000
        record_instant_mix_request(
            store,
            request_id=request_id,
            item_id=item_id,
            seed_track_id=None,
            model=model,
            requested_model=requested_model,
            requested_count=count,
            requested_max_per_artist=max_per_artist,
            requested_exclude_same_album=exclude_same_album,
            effective_count=effective_count,
            max_per_artist=effective_max_per_artist,
            exclude_same_album=effective_exclude_same_album,
            count_collaboration_artists=effective_count_collaboration_artists,
            min_similarity=min_similarity,
            status="failed",
            results=[],
            skipped_without_external_id=0,
            duration_ms=duration_ms,
            error="Navidrome item_id is not synced",
        )
        navidrome_logger.warning(
            "Navidrome similar failed request_id=%s item_id=%s reason=no_external_mapping",
            request_id,
            item_id,
        )
        navidrome_plugin_logger.warning(
            "api_failed request_id=%s item_id=%s model=%s reason=no_external_mapping",
            request_id,
            item_id,
            model,
        )
        raise HTTPException(status_code=404, detail="Navidrome item_id is not synced")

    try:
        candidates = Recommender(store, settings, model).similar(
            seed,
            k=effective_count,
            max_per_artist=effective_max_per_artist,
            exclude_same_album=effective_exclude_same_album,
            count_collaboration_artists=effective_count_collaboration_artists,
        )
    except FileNotFoundError as exc:
        duration_ms = (perf_counter() - started) * 1000
        record_instant_mix_request(
            store,
            request_id=request_id,
            item_id=item_id,
            seed_track_id=seed.id,
            model=model,
            requested_model=requested_model,
            requested_count=count,
            requested_max_per_artist=max_per_artist,
            requested_exclude_same_album=exclude_same_album,
            effective_count=effective_count,
            max_per_artist=effective_max_per_artist,
            exclude_same_album=effective_exclude_same_album,
            count_collaboration_artists=effective_count_collaboration_artists,
            min_similarity=min_similarity,
            status="failed",
            results=[],
            skipped_without_external_id=0,
            duration_ms=duration_ms,
            error=str(exc),
        )
        navidrome_logger.warning(
            "Navidrome similar failed request_id=%s item_id=%s track_id=%s model=%s reason=missing_index error=%s",
            request_id,
            item_id,
            seed.id,
            model,
            exc,
        )
        navidrome_plugin_logger.warning(
            "api_failed request_id=%s item_id=%s track_id=%s model=%s reason=missing_index error=%s",
            request_id,
            item_id,
            seed.id,
            model,
            exc,
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LookupError as exc:
        duration_ms = (perf_counter() - started) * 1000
        record_instant_mix_request(
            store,
            request_id=request_id,
            item_id=item_id,
            seed_track_id=seed.id,
            model=model,
            requested_model=requested_model,
            requested_count=count,
            requested_max_per_artist=max_per_artist,
            requested_exclude_same_album=exclude_same_album,
            effective_count=effective_count,
            max_per_artist=effective_max_per_artist,
            exclude_same_album=effective_exclude_same_album,
            count_collaboration_artists=effective_count_collaboration_artists,
            min_similarity=min_similarity,
            status="failed",
            results=[],
            skipped_without_external_id=0,
            duration_ms=duration_ms,
            error=str(exc),
        )
        navidrome_logger.warning(
            "Navidrome similar failed request_id=%s item_id=%s track_id=%s model=%s reason=missing_embedding error=%s",
            request_id,
            item_id,
            seed.id,
            model,
            exc,
        )
        navidrome_plugin_logger.warning(
            "api_failed request_id=%s item_id=%s track_id=%s model=%s reason=missing_embedding error=%s",
            request_id,
            item_id,
            seed.id,
            model,
            exc,
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        duration_ms = (perf_counter() - started) * 1000
        record_instant_mix_request(
            store,
            request_id=request_id,
            item_id=item_id,
            seed_track_id=seed.id,
            model=model,
            requested_model=requested_model,
            requested_count=count,
            requested_max_per_artist=max_per_artist,
            requested_exclude_same_album=exclude_same_album,
            effective_count=effective_count,
            max_per_artist=effective_max_per_artist,
            exclude_same_album=effective_exclude_same_album,
            count_collaboration_artists=effective_count_collaboration_artists,
            min_similarity=min_similarity,
            status="failed",
            results=[],
            skipped_without_external_id=0,
            duration_ms=duration_ms,
            error=str(exc),
        )
        navidrome_logger.exception(
            "Navidrome similar failed request_id=%s item_id=%s track_id=%s model=%s reason=unexpected",
            request_id,
            item_id,
            seed.id,
            model,
        )
        navidrome_plugin_logger.warning(
            "api_failed request_id=%s item_id=%s track_id=%s model=%s reason=unexpected error=%s",
            request_id,
            item_id,
            seed.id,
            model,
            exc,
        )
        raise HTTPException(status_code=503, detail=f"Navidrome similar failed: {exc}") from exc

    results: list[NavidromeSimilarItem] = []
    skipped_without_external_id = 0
    sorted_candidates = sorted(candidates, key=lambda item: item.similarity, reverse=True)
    for candidate in sorted_candidates:
        if min_similarity is not None and candidate.similarity < float(min_similarity):
            continue
        external_id = store.external_id_for_track("navidrome", candidate.track.id)
        if external_id is None:
            skipped_without_external_id += 1
            continue
        results.append(
            NavidromeSimilarItem(
                item_id=external_id,
                track_id=candidate.track.id,
                artist=candidate.track.artist,
                title=candidate.track.title,
                album=candidate.track.album,
                distance=candidate.distance,
                similarity=candidate.similarity,
            )
        )
        if len(results) >= effective_count:
            break
    if skipped_without_external_id:
        navidrome_logger.warning(
            "Navidrome similar skipped results without external ids request_id=%s item_id=%s skipped=%s",
            request_id,
            item_id,
            skipped_without_external_id,
        )
    duration_ms = (perf_counter() - started) * 1000
    record_instant_mix_request(
        store,
        request_id=request_id,
        item_id=item_id,
        seed_track_id=seed.id,
        model=model,
        requested_model=requested_model,
        requested_count=count,
        requested_max_per_artist=max_per_artist,
        requested_exclude_same_album=exclude_same_album,
        effective_count=effective_count,
        max_per_artist=effective_max_per_artist,
        exclude_same_album=effective_exclude_same_album,
        count_collaboration_artists=effective_count_collaboration_artists,
        min_similarity=min_similarity,
        status="completed",
        results=results,
        skipped_without_external_id=skipped_without_external_id,
        duration_ms=duration_ms,
    )
    navidrome_logger.info(
        "Navidrome similar completed request_id=%s item_id=%s track_id=%s model=%s results=%s skipped_without_external_id=%s duration_ms=%.1f",
        request_id,
        item_id,
        seed.id,
        model,
        len(results),
        skipped_without_external_id,
        duration_ms,
    )
    navidrome_plugin_logger.info(
        "api_completed request_id=%s item_id=%s track_id=%s model=%s results=%s skipped_without_external_id=%s duration_ms=%.1f",
        request_id,
        item_id,
        seed.id,
        model,
        len(results),
        skipped_without_external_id,
        duration_ms,
    )
    return NavidromeSimilarResponse(
        request_id=request_id,
        seed_item_id=item_id,
        seed_track_id=seed.id,
        model=model,
        requested_count=count,
        effective_count=effective_count,
        min_similarity=min_similarity,
        skipped_without_external_id=skipped_without_external_id,
        results=results,
    )


@app.post("/jobs/analyze")
def start_analyze(request: AnalyzeRequest, background_tasks: BackgroundTasks) -> dict[str, object]:
    def start_now(job_id: str, tasks: BackgroundTasks | None) -> dict[str, object]:
        local_executor_enabled = request.execution_mode in {"both", "local"} and request.local_executor_enabled
        if request.model == MUQ_MULAN_MODEL:
            local_executor_enabled = False
        store, _settings = context()
        durable_job = store.create_analysis_job(
            request.model,
            request.limit,
            kind="analyze",
            local_executor_enabled=local_executor_enabled,
            workers=request.workers,
            tf_threads=request.tf_threads,
            max_attempts=request.max_attempts,
            job_id=job_id,
        )
        update_job(
            job_id,
            status="running" if durable_job.total else "completed",
            total=durable_job.total,
            message=(
                f"Queued {durable_job.total} tracks for {request.model}"
                if durable_job.total
                else "Analyzed 0 tracks, failed 0"
            ),
        )
        if local_executor_enabled and durable_job.total:
            args = (
                job_id,
                request.model,
                request.limit,
                request.workers,
                request.tf_threads,
                True,
                request.max_attempts,
                False,
            )
            if tasks is None:
                _analyze_job(*args)
            else:
                tasks.add_task(_analyze_job, *args)
        elif not durable_job.total:
            maybe_start_next_deferred_job()
        return {
            "status": "accepted",
            "job_id": job_id,
            "model": request.model,
            "limit": request.limit,
            "workers": request.workers,
            "tf_threads": request.tf_threads,
            "local_executor_enabled": local_executor_enabled,
            "execution_mode": request.execution_mode,
        }

    store, _settings = context()
    deferred_job_id, deferred = create_deferred_job_if_busy(
        "analyze",
        f"Waiting to analyze {request.model}",
        lambda job_id: lambda: start_now(job_id, None),
        store=store,
    )
    if deferred:
        return {
            "status": "deferred",
            "job_id": deferred_job_id,
            "model": request.model,
            "limit": request.limit,
            "workers": request.workers,
            "tf_threads": request.tf_threads,
            "local_executor_enabled": request.execution_mode != "remote" and request.local_executor_enabled,
            "execution_mode": request.execution_mode,
        }
    job_id = create_job("analyze", f"Waiting to analyze {request.model}")
    return start_now(job_id, background_tasks)


@app.post("/workers/register")
def register_worker(request: WorkerRegisterRequest) -> dict[str, object]:
    store, _settings = context()
    try:
        sqlite_retry(store.expire_analysis_leases)
        sqlite_retry(lambda: store.register_analysis_worker(request.worker_id, request.models))
    except sqlite3.OperationalError as exc:
        raise_worker_sqlite_http_exception(exc, "register")
    return {"status": "ok", "worker_id": request.worker_id, "models": request.models}


@app.post("/workers/heartbeat")
def heartbeat_worker(request: WorkerRegisterRequest) -> dict[str, object]:
    store, _settings = context()
    try:
        wrote = sqlite_retry(
            lambda: store.heartbeat_analysis_worker(
                request.worker_id,
                request.models,
                min_interval_seconds=WORKER_HEARTBEAT_WRITE_INTERVAL_SECONDS,
            )
        )
    except sqlite3.OperationalError as exc:
        raise_worker_sqlite_http_exception(exc, "heartbeat")
    return {
        "status": "ok",
        "worker_id": request.worker_id,
        "models": request.models,
        "heartbeat_written": bool(wrote),
    }


@app.get("/workers")
def list_workers() -> dict[str, object]:
    store, _settings = context()
    return {"workers": [analysis_worker_dict(worker) for worker in store.list_analysis_workers()]}


@app.post("/workers/claim")
def claim_worker_tasks(request: WorkerClaimRequest) -> dict[str, object]:
    store, _settings = context()
    try:
        sqlite_retry(store.expire_analysis_leases)
        sqlite_retry(lambda: store.register_analysis_worker(request.worker_id, request.models))
        tasks = sqlite_retry(
            lambda: store.claim_analysis_tasks(
                request.worker_id,
                request.models,
                limit=request.limit,
                lease_seconds=request.lease_seconds,
            )
        )
    except sqlite3.OperationalError as exc:
        raise_worker_sqlite_http_exception(exc, "claim")
    return {"tasks": [analysis_task_dict(task) for task in tasks]}


@app.get("/workers/tasks/{task_id}/audio")
def get_worker_task_audio(task_id: str) -> FileResponse:
    store, settings = context()
    task = store.get_analysis_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != "leased":
        raise HTTPException(status_code=409, detail=f"Task is not active: {task.status}")
    job_status = store.get_analysis_job_status(task.job_id)
    if job_status != "running":
        raise HTTPException(status_code=409, detail="Task job is not running")
    track = store.get_track(task.track_id)
    if track is None:
        store.fail_analysis_task(
            task_id,
            error="Track not found",
            error_type="LookupError",
            stage="audio",
            retryable=False,
        )
        raise HTTPException(status_code=404, detail="Track not found")
    if track.file_size != task.file_size or track.mtime != task.mtime:
        store.fail_analysis_task(
            task_id,
            error="Track changed after task was created",
            error_type="StaleTaskError",
            stage="audio",
            retryable=False,
        )
        raise HTTPException(status_code=409, detail="Task file identity is stale")
    if is_navidrome_track(track):
        try:
            manager = track_audio_path(store, settings, track)
            path = manager.__enter__()
        except Exception as exc:
            store.fail_analysis_task(
                task_id,
                error=str(exc),
                error_type=type(exc).__name__,
                stage="navidrome-download",
                worker_id=task.lease_owner,
                retryable=True,
            )
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return FileResponse(path, background=BackgroundTask(manager.__exit__, None, None, None))
    path = Path(track.path)
    if not path.exists() or not path.is_file():
        store.mark_track_missing(track.id)
        store.fail_analysis_task(
            task_id,
            error=f"Audio file not found: {track.path}",
            error_type="FileNotFoundError",
            stage="audio",
            retryable=False,
        )
        raise HTTPException(status_code=410, detail="Audio file not mounted or no longer exists")
    return FileResponse(path)


@app.post("/workers/results")
def submit_worker_results(
    request: WorkerSubmitRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    started = perf_counter()
    store, _settings = context()
    try:
        sqlite_retry(store.expire_analysis_leases)
        sqlite_retry(
            lambda: store.update_analysis_worker(
                request.worker_id,
                stage="submitting",
                message=(
                    f"submitting {len(request.results)} embedding, "
                    f"{len(request.feature_results)} feature, {len(request.head_results)} head result(s)"
                ),
            )
        )
    except sqlite3.OperationalError as exc:
        if is_sqlite_locked(exc):
            raise HTTPException(status_code=503, detail="SQLite is busy; retry submit") from exc
        raise

    def reject_task(task_id: str, exc: Exception, log_label: str) -> None:
        if is_sqlite_locked(exc):
            raise HTTPException(status_code=503, detail="SQLite is busy; retry submit") from exc
        rejected.append({"task_id": task_id, "error": str(exc)})
        try:
            sqlite_retry(
                lambda: store.fail_analysis_task(
                    task_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    stage="submit",
                    worker_id=request.worker_id,
                    retryable=False,
                )
            )
        except sqlite3.OperationalError as lock_exc:
            if is_sqlite_locked(lock_exc):
                raise HTTPException(status_code=503, detail="SQLite is busy; retry submit") from lock_exc
            raise
        except Exception:
            logger.exception("Failed to mark worker %s rejected task_id=%s", log_label, task_id)

    completed_embedding_job_ids: set[str] = set()
    completed_job_ids: set[str] = set()

    def accept_embedding(item: WorkerResultItem) -> str:
        vector = decode_worker_vector(item)
        task = validate_worker_task_identity(
            store,
            item.task_id,
            request.worker_id,
            item.track_id,
            item.model_name,
            item.file_size,
            item.mtime,
            expire_leases=False,
        )
        store.save_embedding(task.track_id, task.model_name, vector)
        store.mark_track_available(task.track_id)
        store.complete_analysis_task(
            task.id,
            request.worker_id,
            refresh_job=False,
            update_worker=False,
        )
        completed_embedding_job_ids.add(task.job_id)
        completed_job_ids.add(task.job_id)
        return task.id

    def accept_features(item: WorkerFeatureResultItem) -> str:
        task = validate_worker_task_identity(
            store,
            item.task_id,
            request.worker_id,
            item.track_id,
            item.model_name,
            item.file_size,
            item.mtime,
            expire_leases=False,
        )
        features = [
            TrackFeature(
                name=feature.name,
                value=feature.value,
                text_value=feature.text_value,
                unit=feature.unit,
                confidence=feature.confidence,
                extractor=feature.extractor,
            )
            for feature in item.features
        ]
        store.save_features(task.track_id, features)
        store.mark_track_available(task.track_id)
        store.complete_analysis_task(
            task.id,
            request.worker_id,
            refresh_job=False,
            update_worker=False,
        )
        completed_job_ids.add(task.job_id)
        return task.id

    def accept_heads(item: WorkerHeadResultItem) -> str:
        task = validate_worker_task_identity(
            store,
            item.task_id,
            request.worker_id,
            item.track_id,
            item.model_name,
            item.file_size,
            item.mtime,
            expire_leases=False,
        )
        for output in item.outputs:
            scores = decode_worker_scores(output)
            store.save_model_output(
                task.track_id,
                output.model_name,
                scores,
                output.aggregation,
            )
            store.save_predictions(
                task.track_id,
                output.model_name,
                [
                    TrackPrediction(
                        label=prediction.label,
                        score=prediction.score,
                        rank=prediction.rank,
                    )
                    for prediction in output.predictions
                ],
            )
        store.mark_track_available(task.track_id)
        store.complete_analysis_task(
            task.id,
            request.worker_id,
            refresh_job=False,
            update_worker=False,
        )
        completed_job_ids.add(task.job_id)
        return task.id

    def accept_feature_batch(items: list[WorkerFeatureResultItem]) -> list[str]:
        if not items:
            return []
        now = utc_now()
        accepted_ids: list[str] = []
        with store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for item in items:
                row = conn.execute(
                    """
                    SELECT
                        task.*,
                        track.file_size AS current_file_size,
                        track.mtime AS current_mtime
                    FROM analysis_tasks task
                    JOIN tracks track ON track.id = task.track_id
                    WHERE task.id = ?
                    """,
                    (item.task_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("Task not found")
                if str(row["status"]) != "leased":
                    raise ValueError(f"Task is not active: {row['status']}")
                if row["lease_owner"] != request.worker_id:
                    raise ValueError("Task is not leased by this worker")
                if int(row["track_id"]) != item.track_id or row["model_name"] != item.model_name:
                    raise ValueError("Task result identity mismatch")
                if int(row["file_size"]) != item.file_size or int(row["mtime"]) != item.mtime:
                    raise ValueError("Task result is stale")
                if int(row["current_file_size"]) != item.file_size or int(row["current_mtime"]) != item.mtime:
                    raise ValueError("Track changed after task was created")

                extractors = sorted({feature.extractor for feature in item.features})
                for extractor in extractors:
                    conn.execute(
                        "DELETE FROM track_features WHERE track_id = ? AND extractor = ?",
                        (item.track_id, extractor),
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
                            item.track_id,
                            feature.name,
                            feature.value,
                            feature.text_value,
                            feature.unit,
                            feature.confidence,
                            feature.extractor,
                            now,
                        )
                        for feature in item.features
                    ],
                )
                conn.execute(
                    "UPDATE tracks SET missing_at = NULL, last_seen_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, item.track_id),
                )
                conn.execute(
                    """
                    UPDATE analysis_tasks
                    SET status = 'completed',
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        error = NULL,
                        error_type = NULL,
                        stage = 'completed',
                        updated_at = ?,
                        completed_at = ?
                    WHERE id = ?
                      AND lease_owner = ?
                    """,
                    (now, now, item.task_id, request.worker_id),
                )
                completed_job_ids.add(str(row["job_id"]))
                accepted_ids.append(item.task_id)
        return accepted_ids

    def accept_with_retry(operation):
        try:
            return sqlite_retry(operation)
        except sqlite3.OperationalError as exc:
            if is_sqlite_locked(exc):
                raise HTTPException(status_code=503, detail="SQLite is busy; retry submit") from exc
            raise

    accepted: list[str] = []
    rejected: list[dict[str, str]] = []
    accept_started = perf_counter()
    for item in request.results:
        try:
            accepted.append(accept_with_retry(lambda item=item: accept_embedding(item)))
        except HTTPException:
            raise
        except Exception as exc:
            reject_task(item.task_id, exc, "result")
    if request.feature_results:
        try:
            accepted.extend(accept_with_retry(lambda: accept_feature_batch(request.feature_results)))
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "Batch feature submit failed; falling back to per-item submit worker_id=%s count=%s",
                request.worker_id,
                len(request.feature_results),
            )
            for item in request.feature_results:
                try:
                    accepted.append(accept_with_retry(lambda item=item: accept_features(item)))
                except HTTPException:
                    raise
                except Exception as exc:
                    reject_task(item.task_id, exc, "feature result")
    for item in request.head_results:
        try:
            accepted.append(accept_with_retry(lambda item=item: accept_heads(item)))
        except HTTPException:
            raise
        except Exception as exc:
            reject_task(item.task_id, exc, "head result")
    accept_seconds = perf_counter() - accept_started
    sqlite_retry(
        lambda: store.update_analysis_worker(
            request.worker_id,
            stage="submitted",
            message=f"accepted {len(accepted)}, rejected {len(rejected)} result(s)",
            current_task_id=None,
            completed_delta=len(accepted),
        )
    )
    refresh_started = perf_counter()
    for completed_job_id in sorted(completed_job_ids):
        sqlite_retry(lambda completed_job_id=completed_job_id: store.refresh_analysis_job(completed_job_id))
    refresh_seconds = perf_counter() - refresh_started
    for completed_job_id in sorted(completed_embedding_job_ids):
        schedule_auto_index_for_analysis(store, completed_job_id, background_tasks)
    total_seconds = perf_counter() - started
    if total_seconds >= 1.0 or len(accepted) + len(rejected) >= 16:
        logger.info(
            "Worker submit completed worker_id=%s embeddings=%s features=%s heads=%s "
            "accepted=%s rejected=%s accept_seconds=%.3f refresh_seconds=%.3f total_seconds=%.3f",
            request.worker_id,
            len(request.results),
            len(request.feature_results),
            len(request.head_results),
            len(accepted),
            len(rejected),
            accept_seconds,
            refresh_seconds,
            total_seconds,
        )
    return {"status": "ok", "accepted": accepted, "rejected": rejected}


@app.post("/workers/failures")
def submit_worker_failures(
    request: WorkerFailuresRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    store, _settings = context()
    store.expire_analysis_leases()
    store.update_analysis_worker(
        request.worker_id,
        stage="reporting_failures",
        message=f"reporting {len(request.failures)} failure(s)",
    )
    failed: list[str] = []
    candidate_job_ids: set[str] = set()
    for item in request.failures:
        task = store.get_analysis_task(item.task_id)
        if task is not None:
            candidate_job_ids.add(task.job_id)
        store.fail_analysis_task(
            item.task_id,
            error=item.error,
            error_type=item.error_type,
            stage=item.stage,
            worker_id=request.worker_id,
            retryable=item.retryable,
        )
        failed.append(item.task_id)
    store.update_analysis_worker(
        request.worker_id,
        stage="failures_submitted",
        message=f"accepted {len(failed)} failure(s)",
        current_task_id=None,
    )
    for completed_job_id in sorted(candidate_job_ids):
        schedule_auto_index_for_analysis(store, completed_job_id, background_tasks)
    return {"status": "ok", "failed": failed}


@app.post("/workers/release")
def release_worker_tasks(request: WorkerReleaseRequest) -> dict[str, object]:
    store, _settings = context()
    store.expire_analysis_leases()
    released = store.release_analysis_tasks(request.worker_id, request.task_ids)
    return {"status": "ok", "released": released}


@app.post("/models/download-head-pack")
def download_head_pack() -> dict[str, object]:
    _store, settings = context()
    results = download_head_pack_models(settings)
    return {
        "status": "ok",
        "downloaded": [str(result.path) for result in results if result.downloaded],
        "already_present": [str(result.path) for result in results if not result.downloaded],
        "head_pack": head_pack_readiness(settings),
    }


@app.post("/jobs/download-head-models")
def start_download_head_models(background_tasks: BackgroundTasks) -> dict[str, object]:
    deferred_job_id, deferred = create_deferred_job_if_busy(
        "download-head-models",
        "Waiting to download head models",
        lambda job_id: lambda: _download_head_models_job(job_id),
    )
    if deferred:
        return {"status": "deferred", "job_id": deferred_job_id}
    job_id = create_job("download-head-models", "Waiting to download head models")
    background_tasks.add_task(_download_head_models_job, job_id)
    return {"status": "accepted", "job_id": job_id}


@app.post("/jobs/analyze-heads")
def start_analyze_heads(
    request: AnalyzeHeadsRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    def start_now(job_id: str, tasks: BackgroundTasks | None) -> dict[str, object]:
        local_enabled = request.execution_mode in {"both", "local"} and request.local_executor_enabled
        store, _settings = context()
        head_model_names = [head.id for head in DISCOGS_EFFNET_HEADS]
        tracks = store.list_tracks_missing_head_pack(head_model_names, limit=request.limit)
        durable_job = store.create_analysis_job(
            "discogs-effnet-heads",
            request.limit,
            kind="analyze-heads",
            tracks=tracks,
            local_executor_enabled=local_enabled,
            max_attempts=request.max_attempts,
            job_id=job_id,
        )
        update_job(
            job_id,
            status="running" if durable_job.total else "completed",
            total=durable_job.total,
            message=(
                f"Queued {durable_job.total} head analysis tasks"
                if durable_job.total
                else "Analyzed heads for 0 tracks, failed 0"
            ),
        )
        if local_enabled and durable_job.total:
            args = (job_id, request.limit, True, request.max_attempts, False)
            if tasks is None:
                _analyze_heads_job(*args)
            else:
                tasks.add_task(_analyze_heads_job, *args)
        elif not durable_job.total:
            maybe_start_next_deferred_job()
        return {
            "status": "accepted",
            "job_id": job_id,
            "limit": request.limit,
            "execution_mode": request.execution_mode,
            "local_executor_enabled": local_enabled,
        }

    store, _settings = context()
    deferred_job_id, deferred = create_deferred_job_if_busy(
        "analyze-heads",
        "Waiting to analyze Discogs-EffNet heads",
        lambda job_id: lambda: start_now(job_id, None),
        store=store,
    )
    if deferred:
        return {
            "status": "deferred",
            "job_id": deferred_job_id,
            "limit": request.limit,
            "execution_mode": request.execution_mode,
            "local_executor_enabled": request.execution_mode != "remote" and request.local_executor_enabled,
        }
    job_id = create_job("analyze-heads", "Waiting to analyze Discogs-EffNet heads")
    return start_now(job_id, background_tasks)


@app.post("/jobs/analyze-audio-features")
def start_analyze_audio_features(
    request: AnalyzeAudioFeaturesRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    extractor = request.extractor.strip() or AUDIO_FEATURE_EXTRACTOR
    if extractor != AUDIO_FEATURE_EXTRACTOR:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio feature extractor: {extractor}",
        )

    def start_now(job_id: str, tasks: BackgroundTasks | None) -> dict[str, object]:
        local_enabled = request.execution_mode in {"both", "local"} and request.local_executor_enabled
        store, _settings = context()
        if request.reset_existing:
            tracks = store.list_active_tracks(limit=request.limit)
            deleted_features = store.delete_features_for_tracks(
                [track.id for track in tracks],
                extractor,
            )
            logger.info(
                "Reset audio features before analysis tracks=%s deleted_features=%s extractor=%s",
                len(tracks),
                deleted_features,
                extractor,
            )
        else:
            tracks = store.list_tracks_missing_features(extractor, limit=request.limit)
            deleted_features = 0
        clear_stats_cache()
        durable_job = store.create_analysis_job(
            extractor,
            request.limit,
            kind="analyze-audio-features",
            tracks=tracks,
            local_executor_enabled=local_enabled,
            max_attempts=request.max_attempts,
            job_id=job_id,
        )
        update_job(
            job_id,
            status="running" if durable_job.total else "completed",
            total=durable_job.total,
            message=(
                f"Queued {durable_job.total} audio feature tasks"
                if durable_job.total
                else "Analyzed audio features for 0 tracks, failed 0"
            ),
        )
        if local_enabled and durable_job.total:
            args = (job_id, request.limit, request.workers, True, request.max_attempts, False)
            if tasks is None:
                _analyze_audio_features_job(*args)
            else:
                tasks.add_task(_analyze_audio_features_job, *args)
        elif not durable_job.total:
            maybe_start_next_deferred_job()
        return {
            "status": "accepted",
            "job_id": job_id,
            "limit": request.limit,
            "workers": request.workers,
            "execution_mode": request.execution_mode,
            "local_executor_enabled": local_enabled,
            "reset_existing": request.reset_existing,
            "deleted_features": deleted_features,
            "extractor": extractor,
        }

    store, _settings = context()
    deferred_job_id, deferred = create_deferred_job_if_busy(
        "analyze-audio-features",
        "Waiting to analyze audio features",
        lambda job_id: lambda: start_now(job_id, None),
        store=store,
    )
    if deferred:
        return {
            "status": "deferred",
            "job_id": deferred_job_id,
            "limit": request.limit,
            "workers": request.workers,
            "execution_mode": request.execution_mode,
            "local_executor_enabled": request.execution_mode != "remote" and request.local_executor_enabled,
            "reset_existing": request.reset_existing,
            "deleted_features": 0,
            "extractor": extractor,
        }
    job_id = create_job("analyze-audio-features", "Waiting to analyze audio features")
    return start_now(job_id, background_tasks)


@app.post("/jobs/analyze-genres")
def start_analyze_genres_compat(
    request: AnalyzeHeadsRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    return start_analyze_heads(request, background_tasks)


@app.post("/jobs/index")
def start_index(request: IndexRequest, background_tasks: BackgroundTasks) -> dict[str, object]:
    deferred_job_id, deferred = create_deferred_job_if_busy(
        "index",
        f"Waiting to build index for {request.model}",
        lambda job_id: lambda: _index_job(job_id, request.model),
    )
    if deferred:
        return {"status": "deferred", "job_id": deferred_job_id, "model": request.model}
    job_id = create_job("index", f"Waiting to build index for {request.model}")
    background_tasks.add_task(_index_job, job_id, request.model)
    return {"status": "accepted", "job_id": job_id, "model": request.model}


@app.post("/jobs/check-missing-files")
def start_check_missing_files(background_tasks: BackgroundTasks) -> dict[str, object]:
    deferred_job_id, deferred = create_deferred_job_if_busy(
        "check-missing-files",
        "Waiting to check file availability",
        lambda job_id: lambda: _check_missing_files_job(job_id),
    )
    if deferred:
        return {"status": "deferred", "job_id": deferred_job_id}
    job_id = create_job("check-missing-files", "Waiting to check file availability")
    background_tasks.add_task(_check_missing_files_job, job_id)
    return {"status": "accepted", "job_id": job_id}


@app.post("/jobs/navidrome-sync")
def start_navidrome_sync(
    request: NavidromeSyncRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    def start_now(job_id: str, tasks: BackgroundTasks | None) -> dict[str, object]:
        store, _settings = context()
        known_total = request.limit or store.count_external_tracks("navidrome") or 0
        job = store.create_progress_job(
            "navidrome-sync",
            "navidrome",
            total=known_total,
            message="Waiting to sync Navidrome catalog",
            job_id=job_id,
        )
        args = (job.id, request.page_size, request.limit, request.mark_stale)
        if tasks is None:
            _navidrome_sync_job(*args)
        else:
            tasks.add_task(_navidrome_sync_job, *args)
        return {
            "status": "accepted",
            "job_id": job.id,
            "page_size": request.page_size,
            "limit": request.limit,
            "mark_stale": request.mark_stale,
        }

    store, _settings = context()
    deferred_job_id, deferred = create_deferred_job_if_busy(
        "navidrome-sync",
        "Waiting to sync Navidrome catalog",
        lambda job_id: lambda: start_now(job_id, None),
        store=store,
    )
    if deferred:
        return {
            "status": "deferred",
            "job_id": deferred_job_id,
            "page_size": request.page_size,
            "limit": request.limit,
            "mark_stale": request.mark_stale,
        }
    job_id = create_job("navidrome-sync", "Waiting to sync Navidrome catalog")
    return start_now(job_id, background_tasks)


@app.get("/jobs")
def list_jobs(
    include_completed: bool = False,
    detail: bool = False,
    include_workers: bool = False,
) -> dict[str, object]:
    store, _settings = context()
    statuses = None if include_completed else sorted(ACTIVE_JOB_STATUSES)
    recent_jobs = store.recent_analysis_jobs(limit=100 if include_completed else 20, statuses=statuses)
    sync_memory_jobs_from_durable_jobs(recent_jobs)
    workers = store.list_analysis_workers() if detail else []
    durable_jobs = {
        job.id: analysis_job_status_dict(job, store, detail=detail, workers=workers)
        for job in recent_jobs[:20]
    }
    with JOBS_LOCK:
        now = perf_counter()
        queue_positions = {job_id: index + 1 for index, job_id in enumerate(DEFERRED_JOB_ORDER)}
        jobs = []
        for job in JOBS.values():
            if job.id in durable_jobs:
                continue
            if not include_completed and job.status not in {"queued", "running", "deferred"}:
                continue
            data = asdict(job)
            if job.status in {"queued", "running"}:
                data["elapsed_seconds"] = max(0.0, now - job.started_at)
            elif job.finished_at is not None:
                data["elapsed_seconds"] = max(0.0, job.finished_at - job.started_at)
                data["eta_seconds"] = None
            if job.status == "deferred":
                data["queue_position"] = queue_positions.get(job.id)
                data["status_hint"] = "Waiting for previous job to finish"
            jobs.append(data)
    jobs.extend(durable_jobs.values())
    jobs.sort(
        key=lambda job: float(job.get("created_at_epoch") or job.get("started_at") or 0.0),
        reverse=True,
    )
    response: dict[str, object] = {"jobs": jobs[:20]}
    if include_workers:
        response["workers"] = [analysis_worker_dict(worker) for worker in store.list_analysis_workers()]
    return response


@app.get("/jobs/{job_id}")
def get_job_detail(job_id: str) -> dict[str, object]:
    store, _settings = context()
    job = store.get_analysis_job(job_id)
    if job is None:
        with JOBS_LOCK:
            memory_job = JOBS.get(job_id)
        if memory_job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return {"job": asdict(memory_job), "tasks": []}
    statuses = ["leased", "queued", "failed_retryable", "final_failed"]
    tasks = store.list_analysis_job_tasks(job_id, statuses=statuses, limit=200)
    return {
        "job": analysis_job_status_dict(job, store, detail=True),
        "tasks": [analysis_task_dict(task) for task in tasks],
    }


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: CancelJobRequest | None = None) -> dict[str, object]:
    reason = request.reason if request is not None else "Cancelled by user"
    store, _settings = context()
    durable_job = store.cancel_analysis_job(job_id, reason)
    was_deferred = False
    with JOBS_LOCK:
        memory_job = JOBS.get(job_id)
        if memory_job is not None:
            was_deferred = memory_job.status == "deferred"
            memory_job.status = "cancelled"
            memory_job.message = reason
            memory_job.current = None
            memory_job.finished_at = perf_counter()
    if was_deferred:
        with DEFERRED_JOBS_LOCK:
            DEFERRED_JOB_STARTERS.pop(job_id, None)
            if job_id in DEFERRED_JOB_ORDER:
                DEFERRED_JOB_ORDER.remove(job_id)
        maybe_start_next_deferred_job()
    if durable_job is None and memory_job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "status": "cancelled",
        "job_id": job_id,
        "job": analysis_job_status_dict(durable_job, store) if durable_job is not None else asdict(memory_job),
    }


@app.post("/index/rebuild")
def rebuild_index(request: IndexRequest) -> dict[str, object]:
    store, settings = context()
    try:
        logger.info("Rebuilding index via API model=%s", request.model)
        path = build_index(store, settings, request.model)
    except ValueError as exc:
        logger.warning("Index rebuild failed model=%s error=%s", request.model, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "model": request.model, "index": str(path)}


@app.post("/feedback")
def save_feedback(request: FeedbackRequest) -> dict[str, str]:
    store, _settings = context()
    if store.get_track(request.seed_track_id) is None:
        logger.warning("Feedback seed track not found seed_track_id=%s", request.seed_track_id)
        raise HTTPException(status_code=404, detail="Seed track not found")
    if store.get_track(request.result_track_id) is None:
        logger.warning("Feedback result track not found result_track_id=%s", request.result_track_id)
        raise HTTPException(status_code=404, detail="Result track not found")
    try:
        store.save_feedback(
            request.seed_track_id,
            request.result_track_id,
            request.model,
            request.rating,
            request.note,
        )
    except ValueError as exc:
        logger.warning("Feedback validation failed seed_track_id=%s result_track_id=%s model=%s error=%s", request.seed_track_id, request.result_track_id, request.model, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(
        "Saved feedback seed_track_id=%s result_track_id=%s model=%s rating=%s",
        request.seed_track_id,
        request.result_track_id,
        request.model,
        request.rating,
    )
    return {"status": "ok"}


def _check_missing_files_job(job_id: str) -> None:
    try:
        store, _settings = context()
        total = store.count_tracks()
        logger.info("Starting missing-file check job_id=%s total=%s", job_id, total)
        update_job(
            job_id,
            status="running",
            total=total,
            message=f"Checking availability for {total} tracks",
        )
        checked, missing = store.check_file_availability()
        update_job(job_id, done=checked, failed=0)
        finish_job(job_id, "completed", f"Checked {checked} tracks, lost files {missing}")
        logger.info("Finished missing-file check job_id=%s checked=%s missing=%s", job_id, checked, missing)
    except Exception as exc:
        logger.exception("Missing-file check failed job_id=%s", job_id)
        finish_job(job_id, "failed", str(exc))


def _navidrome_sync_job(
    job_id: str,
    page_size: int,
    limit: int | None,
    mark_stale: bool,
) -> None:
    store: Store | None = None
    try:
        store, settings = context()
        client = NavidromeClient(settings.navidrome)
        logger.info(
            "Starting Navidrome sync job job_id=%s page_size=%s limit=%s mark_stale=%s",
            job_id,
            page_size,
            limit,
            mark_stale,
        )
        known_total = limit or store.count_external_tracks("navidrome") or 0
        store.update_progress_job(
            job_id,
            status="running",
            total=known_total,
            message="Syncing Navidrome catalog",
        )

        def progress(count, song) -> None:
            nonlocal known_total
            if count > known_total:
                known_total = count
            if count == 1 or count % 25 == 0:
                current = f"{song.id} {song.artist or ''} - {song.title or ''}".strip()
                store.update_progress_job(
                    job_id,
                    done=count,
                    total=known_total,
                    message=f"Synced {count} Navidrome songs; current {current}",
                )

        result = sync_navidrome_catalog(
            store,
            client,
            page_size=page_size,
            limit=limit,
            mark_stale=mark_stale,
            progress=progress,
        )
        status = "failed" if result.failed_count else "completed"
        store.update_progress_job(
            job_id,
            done=result.seen_count,
            failed=result.failed_count,
            total=max(known_total, result.seen_count),
            status=status,
            message=f"Navidrome sync {result.summary()}",
            finished=True,
        )
        logger.info("Finished Navidrome sync job job_id=%s %s", job_id, result.summary())
        maybe_start_next_deferred_job()
    except Exception as exc:
        logger.exception("Navidrome sync job failed job_id=%s", job_id)
        if store is not None:
            store.update_progress_job(job_id, status="failed", message=str(exc), finished=True)
            maybe_start_next_deferred_job()
        else:
            finish_job(job_id, "failed", str(exc))


@dataclass(frozen=True)
class AnalyzeResult:
    task_id: str | None
    track_id: int
    path: str
    status: str
    vector: np.ndarray | None = None
    error: str | None = None
    error_type: str | None = None
    traceback: str | None = None
    stage: str | None = None


@dataclass(frozen=True)
class HeadAnalyzeResult:
    track_id: int
    path: str
    status: str
    outputs: list[HeadOutput] | None = None
    error: str | None = None
    error_type: str | None = None
    traceback: str | None = None
    stage: str | None = None


@dataclass(frozen=True)
class AudioFeaturesResult:
    track_id: int
    path: str
    status: str
    features: list | None = None
    error: str | None = None
    error_type: str | None = None
    traceback: str | None = None
    stage: str | None = None


def analyze_failure_fields(exc: Exception, stage: str) -> dict[str, str]:
    return {
        "error": str(exc),
        "error_type": type(exc).__name__,
        "traceback": traceback.format_exc(),
        "stage": stage,
    }


def embedding_failure_stage(exc: Exception) -> str:
    message = str(exc).lower()
    if any(token in message for token in ["ffmpeg", "audio", "codec", "sample", "decode"]):
        return "load_audio"
    return "predict"


def mark_missing_after_failure(store: Store, result: AnalyzeResult | HeadAnalyzeResult | AudioFeaturesResult) -> None:
    if result.error_type == "FileNotFoundError":
        store.mark_track_missing(result.track_id)


def analyze_failure_retryable(result: AnalyzeResult | HeadAnalyzeResult | AudioFeaturesResult) -> bool:
    if result.error_type == "FileNotFoundError":
        return False
    text = (result.error or "").lower()
    terminal_fragments = (
        "torch is required for muq-mulan",
        "muq is required for muq-mulan",
        "muq-mulan model could not be loaded",
        "model file not found",
        "embedding vector has zero norm",
    )
    return not any(fragment in text for fragment in terminal_fragments)


def create_analyze_embedder(settings: Settings, model: str) -> object:
    if model == MUQ_MULAN_MODEL:
        return create_track_embedder(settings, model)
    return DiscogsEffnetEmbedder(settings, model)


_WORKER_EMBEDDER: object | None = None
_WORKER_AUDIO_FEATURE_ANALYZER: AudioFeatureAnalyzer | None = None


def configure_analyze_runtime(tf_threads: int) -> None:
    os.environ["TF_NUM_INTRAOP_THREADS"] = str(tf_threads)
    os.environ["TF_NUM_INTEROP_THREADS"] = "1"
    os.environ["OMP_NUM_THREADS"] = str(tf_threads)


def _init_embedding_worker(settings: Settings, model: str, tf_threads: int) -> None:
    global _WORKER_EMBEDDER
    configure_analyze_runtime(tf_threads)
    _WORKER_EMBEDDER = create_analyze_embedder(settings, model)
    analysis_logger.info("Initialized embedding worker model=%s tf_threads=%s", model, tf_threads)


def _extract_embedding_worker(task_id: str | None, track_id: int, path: str) -> AnalyzeResult:
    if _WORKER_EMBEDDER is None:
        raise RuntimeError("Embedding worker was not initialized")
    try:
        vector = _WORKER_EMBEDDER.extract_track_vector(Path(path))
        return AnalyzeResult(task_id=task_id, track_id=track_id, path=path, status="ok", vector=vector)
    except Exception as exc:
        return AnalyzeResult(
            task_id=task_id,
            track_id=track_id,
            path=path,
            status="failed",
            **analyze_failure_fields(exc, embedding_failure_stage(exc)),
        )


def _init_audio_feature_worker() -> None:
    global _WORKER_AUDIO_FEATURE_ANALYZER
    os.environ.setdefault("DISCOCS_FFMPEG_THREADS", "1")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    _WORKER_AUDIO_FEATURE_ANALYZER = AudioFeatureAnalyzer()
    analysis_logger.info("Initialized audio feature worker extractor=%s", AUDIO_FEATURE_EXTRACTOR)


def _extract_audio_features_worker(track_id: int, path: str) -> AudioFeaturesResult:
    if _WORKER_AUDIO_FEATURE_ANALYZER is None:
        raise RuntimeError("Audio feature worker was not initialized")
    try:
        features = _WORKER_AUDIO_FEATURE_ANALYZER.analyze_track(Path(path))
        return AudioFeaturesResult(
            track_id=track_id,
            path=path,
            status="ok",
            features=features,
        )
    except Exception as exc:
        return AudioFeaturesResult(
            track_id=track_id,
            path=path,
            status="failed",
            **analyze_failure_fields(exc, "audio_features"),
        )


def _extract_embedding_local(
    embedder: object,
    store: Store,
    settings: Settings,
    track: Track,
) -> AnalyzeResult:
    audio_path, manager, failure = _prepare_analyze_audio_path(store, settings, track)
    if failure is not None:
        return failure
    try:
        vector = embedder.extract_track_vector(audio_path)
        return AnalyzeResult(task_id=None, track_id=track.id, path=track.path, status="ok", vector=vector)
    except Exception as exc:
        return AnalyzeResult(
            task_id=None,
            track_id=track.id,
            path=track.path,
            status="failed",
            **analyze_failure_fields(exc, embedding_failure_stage(exc)),
        )
    finally:
        _cleanup_audio_manager(manager)


def _prepare_analyze_audio_path(
    store: Store,
    settings: Settings,
    track: Track,
) -> tuple[Path | None, object | None, AnalyzeResult | None]:
    manager = track_audio_path(store, settings, track)
    try:
        return manager.__enter__(), manager, None
    except Exception as exc:
        return (
            None,
            None,
            AnalyzeResult(
                task_id=None,
                track_id=track.id,
                path=track.path,
                status="failed",
                **analyze_failure_fields(exc, "navidrome-download" if is_navidrome_track(track) else embedding_failure_stage(exc)),
            ),
        )


def _cleanup_audio_manager(manager: object | None) -> None:
    if manager is None:
        return
    try:
        manager.__exit__(None, None, None)
    except Exception:
        logger.debug("Audio source cleanup failed", exc_info=True)


def register_analyze_executor(executor: ProcessPoolExecutor) -> None:
    with ANALYZE_EXECUTORS_LOCK:
        ANALYZE_EXECUTORS.add(executor)


def unregister_analyze_executor(executor: ProcessPoolExecutor) -> None:
    with ANALYZE_EXECUTORS_LOCK:
        ANALYZE_EXECUTORS.discard(executor)


def terminate_process_pool(executor: ProcessPoolExecutor) -> None:
    processes = list((getattr(executor, "_processes", {}) or {}).values())
    try:
        executor.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass
    for process in processes:
        try:
            if process.is_alive():
                process.terminate()
        except Exception:
            pass
    for process in processes:
        try:
            process.join(timeout=1)
        except Exception:
            pass
    for process in processes:
        try:
            if process.is_alive():
                process.kill()
        except Exception:
            pass


def _iter_analyze_results(
    tracks: list[Track],
    store: Store,
    settings: Settings,
    model: str,
    workers: int,
    tf_threads: int,
):
    if SHUTDOWN_REQUESTED:
        return
    configure_analyze_runtime(tf_threads)
    if workers <= 1 or model == MUQ_MULAN_MODEL:
        embedder = create_analyze_embedder(settings, model)
        for track in tracks:
            if SHUTDOWN_REQUESTED:
                return
            yield _extract_embedding_local(embedder, store, settings, track)
        return

    executor = ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_embedding_worker,
        initargs=(settings, model, tf_threads),
        mp_context=multiprocessing.get_context("spawn"),
    )
    register_analyze_executor(executor)
    audio_managers: list[object] = []
    try:
        future_to_track = {}
        for track in tracks:
            audio_path, manager, failure = _prepare_analyze_audio_path(store, settings, track)
            if failure is not None:
                yield failure
                continue
            audio_managers.append(manager)
            future = executor.submit(_extract_embedding_worker, None, track.id, str(audio_path))
            future_to_track[future] = track
        for future in as_completed(future_to_track):
            if SHUTDOWN_REQUESTED:
                break
            track = future_to_track[future]
            try:
                yield future.result()
            except Exception as exc:
                yield AnalyzeResult(
                    task_id=None,
                    track_id=track.id,
                    path=track.path,
                    status="failed",
                    **analyze_failure_fields(exc, "predict"),
                )
    finally:
        for manager in audio_managers:
            _cleanup_audio_manager(manager)
        unregister_analyze_executor(executor)
        if SHUTDOWN_REQUESTED:
            terminate_process_pool(executor)
        else:
            try:
                executor.shutdown(wait=True, cancel_futures=False)
            except Exception:
                pass


def task_to_track(task: AnalysisTask) -> Track:
    return Track(
        id=task.track_id,
        path=task.path,
        artist=None,
        title=None,
        album=None,
        duration=None,
        file_size=task.file_size,
        mtime=task.mtime,
    )


def _iter_analyze_task_results(
    tasks: list[AnalysisTask],
    store: Store,
    settings: Settings,
    model: str,
    workers: int,
    tf_threads: int,
):
    if SHUTDOWN_REQUESTED:
        return
    configure_analyze_runtime(tf_threads)
    if workers <= 1 or model == MUQ_MULAN_MODEL:
        embedder = create_analyze_embedder(settings, model)
        for task in tasks:
            if SHUTDOWN_REQUESTED:
                return
            result = _extract_embedding_local(embedder, store, settings, task_to_track(task))
            yield replace(result, task_id=task.id)
        return

    executor = ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_embedding_worker,
        initargs=(settings, model, tf_threads),
        mp_context=multiprocessing.get_context("spawn"),
    )
    register_analyze_executor(executor)
    audio_managers: list[object] = []
    try:
        future_to_task = {}
        for task in tasks:
            track = task_to_track(task)
            audio_path, manager, failure = _prepare_analyze_audio_path(store, settings, track)
            if failure is not None:
                yield replace(failure, task_id=task.id)
                continue
            audio_managers.append(manager)
            future = executor.submit(_extract_embedding_worker, task.id, task.track_id, str(audio_path))
            future_to_task[future] = task
        for future in as_completed(future_to_task):
            if SHUTDOWN_REQUESTED:
                break
            task = future_to_task[future]
            try:
                yield future.result()
            except Exception as exc:
                yield AnalyzeResult(
                    task_id=task.id,
                    track_id=task.track_id,
                    path=task.path,
                    status="failed",
                    **analyze_failure_fields(exc, "predict"),
                )
    finally:
        for manager in audio_managers:
            _cleanup_audio_manager(manager)
        unregister_analyze_executor(executor)
        if SHUTDOWN_REQUESTED:
            terminate_process_pool(executor)
        else:
            try:
                executor.shutdown(wait=True, cancel_futures=False)
            except Exception:
                pass


def _analyze_job(
    job_id: str,
    model: str,
    limit: int | None,
    workers: int = DEFAULT_ANALYZE_WORKERS,
    tf_threads: int = DEFAULT_ANALYZE_TF_THREADS,
    local_executor_enabled: bool = True,
    max_attempts: int = 3,
    enqueue: bool = True,
) -> None:
    try:
        if SHUTDOWN_REQUESTED:
            finish_job(job_id, "failed", "Analyze cancelled during application shutdown")
            return
        store, settings = context()
        if enqueue:
            durable_job = store.create_analysis_job(
                model,
                limit,
                kind="analyze",
                local_executor_enabled=local_executor_enabled,
                workers=workers,
                tf_threads=tf_threads,
                max_attempts=max_attempts,
                job_id=job_id,
            )
        else:
            durable_job = store.get_analysis_job(job_id)
            if durable_job is None:
                raise ValueError(f"Analysis job not found: {job_id}")
        total = durable_job.total
        started_at = perf_counter()
        analysis_logger.info(
            "Starting analyze job job_id=%s model=%s limit=%s workers=%s tf_threads=%s local_executor=%s total=%s",
            job_id,
            model,
            limit,
            workers,
            tf_threads,
            local_executor_enabled,
            total,
        )
        update_job(
            job_id,
            status="running",
            total=total,
            message=(
                f"Analyzing {total} tracks with {model} on {workers} worker(s), "
                f"tf_threads={tf_threads}"
            ),
            **analyze_progress(started_at, total, 0, 0),
        )
        if not local_executor_enabled or total == 0:
            if total == 0:
                finish_job(job_id, "completed", "Analyzed 0 tracks, failed 0")
            else:
                update_job(job_id, status="running", message=f"Queued {total} tracks for remote workers")
            return

        local_worker_id = f"local-{job_id}"
        done = 0
        failed = 0
        while True:
            if SHUTDOWN_REQUESTED:
                finish_job(job_id, "failed", "Analyze cancelled during application shutdown")
                return
            tasks = store.claim_analysis_tasks(
                local_worker_id,
                [model],
                limit=max(workers, 1),
                lease_seconds=3600,
            )
            if not tasks:
                durable_job = store.get_analysis_job(job_id)
                if durable_job is None or durable_job.status not in ACTIVE_JOB_STATUSES:
                    break
                update_job(
                    job_id,
                    done=durable_job.done,
                    failed=durable_job.failed,
                    message=durable_job.message,
                    **analyze_progress(started_at, total, durable_job.done, durable_job.failed),
                )
                time.sleep(2)
                continue
            for result in _iter_analyze_task_results(tasks, store, settings, model, workers, tf_threads):
                if SHUTDOWN_REQUESTED:
                    finish_job(job_id, "failed", "Analyze cancelled during application shutdown")
                    return
                update_job(job_id, current=result.path, message=f"Analyzing {result.path}")
                if result.task_id is None:
                    continue
                if result.status == "ok" and result.vector is not None:
                    try:
                        store.save_embedding(result.track_id, model, result.vector)
                        store.mark_track_available(result.track_id)
                        store.complete_analysis_task(result.task_id, local_worker_id)
                    except Exception as exc:
                        store.fail_analysis_task(
                            result.task_id,
                            error=str(exc),
                            error_type=type(exc).__name__,
                            stage="save",
                            worker_id=local_worker_id,
                            retryable=False,
                        )
                        analysis_logger.exception(
                            "Analyze save failed job_id=%s track_id=%s path=%s model=%s stage=save",
                            job_id,
                            result.track_id,
                            result.path,
                            model,
                        )
                else:
                    mark_missing_after_failure(store, result)
                    store.fail_analysis_task(
                        result.task_id,
                        error=result.error or "Analyze failed",
                        error_type=result.error_type or "AnalyzeError",
                        stage=result.stage or "predict",
                        worker_id=local_worker_id,
                        retryable=analyze_failure_retryable(result),
                    )
                    analysis_logger.error(
                        "Analyze track failed job_id=%s track_id=%s path=%s model=%s stage=%s error_type=%s error=%s\n%s",
                        job_id,
                        result.track_id,
                        result.path,
                        model,
                        result.stage,
                        result.error_type,
                        result.error,
                        result.traceback or "",
                    )
                durable_job = store.get_analysis_job(job_id)
                if durable_job is not None:
                    done = durable_job.done
                    failed = durable_job.failed
                    update_job(
                        job_id,
                        done=done,
                        failed=failed,
                        current=result.path,
                        message=durable_job.message,
                        **analyze_progress(started_at, total, done, failed),
                    )
                    if (done + failed) % 25 == 0 or done + failed == total:
                        progress = analyze_progress(started_at, total, done, failed)
                        analysis_logger.info(
                            "Analyze progress job_id=%s model=%s done=%s failed=%s total=%s elapsed=%.1f tracks_per_min=%s eta_seconds=%s",
                            job_id,
                            model,
                            done,
                            failed,
                            total,
                            progress["elapsed_seconds"],
                            progress["tracks_per_min"],
                            progress["eta_seconds"],
                        )
        durable_job = store.get_analysis_job(job_id)
        if durable_job is not None:
            done = durable_job.done
            failed = durable_job.failed
        analysis_logger.info(
            "Finished analyze job job_id=%s model=%s done=%s failed=%s total=%s",
            job_id,
            model,
            done,
            failed,
            total,
        )
        finish_job(job_id, "completed", f"Analyzed {done} tracks, failed {failed}")
        schedule_auto_index_for_analysis(store, job_id)
    except Exception as exc:
        analysis_logger.exception("Analyze job failed job_id=%s model=%s", job_id, model)
        finish_job(job_id, "failed", str(exc))


def _extract_heads_local(
    analyzer: DiscogsEffnetHeadPackAnalyzer,
    store: Store,
    settings: Settings,
    track: Track,
) -> HeadAnalyzeResult:
    audio_path, manager, failure = _prepare_analyze_audio_path(store, settings, track)
    if failure is not None:
        return HeadAnalyzeResult(
            track_id=track.id,
            path=track.path,
            status="failed",
            error=failure.error,
            error_type=failure.error_type,
            traceback=failure.traceback,
            stage=failure.stage,
        )
    try:
        outputs = analyzer.analyze_track(audio_path)
        return HeadAnalyzeResult(
            track_id=track.id,
            path=track.path,
            status="ok",
            outputs=outputs,
        )
    except Exception as exc:
        return HeadAnalyzeResult(
            track_id=track.id,
            path=track.path,
            status="failed",
            **analyze_failure_fields(exc, "analyze_heads"),
        )
    finally:
        _cleanup_audio_manager(manager)


def _analyze_heads_job(
    job_id: str,
    limit: int | None,
    local_executor_enabled: bool = True,
    max_attempts: int = 3,
    enqueue: bool = True,
) -> None:
    try:
        if SHUTDOWN_REQUESTED:
            finish_job(job_id, "failed", "Head analysis cancelled during application shutdown")
            return
        store, settings = context()
        head_model_names = [head.id for head in DISCOGS_EFFNET_HEADS]
        if enqueue:
            tracks = store.list_tracks_missing_head_pack(head_model_names, limit=limit)
            durable_job = store.create_analysis_job(
                "discogs-effnet-heads",
                limit,
                kind="analyze-heads",
                tracks=tracks,
                local_executor_enabled=local_executor_enabled,
                max_attempts=max_attempts,
                job_id=job_id,
            )
        else:
            durable_job = store.get_analysis_job(job_id)
            if durable_job is None:
                raise ValueError(f"Head analysis job not found: {job_id}")
        total = durable_job.total
        started_at = perf_counter()
        analysis_logger.info(
            "Starting analyze-heads job job_id=%s limit=%s total=%s heads=%s",
            job_id,
            limit,
            total,
            len(head_model_names),
        )
        update_job(
            job_id,
            status="running",
            total=total,
            message=f"Analyzing {total} tracks with Discogs-EffNet heads",
            **analyze_progress(started_at, total, 0, 0),
        )
        done = 0
        failed = 0
        if not local_executor_enabled or total == 0:
            if total == 0:
                finish_job(job_id, "completed", "Analyzed heads for 0 tracks, failed 0")
            else:
                update_job(job_id, status="running", message=f"Queued {total} head analysis tasks for remote workers")
            return
        analyzer = DiscogsEffnetHeadPackAnalyzer(settings)
        local_worker_id = f"local-{job_id}"
        while True:
            if SHUTDOWN_REQUESTED:
                finish_job(job_id, "failed", "Head analysis cancelled during application shutdown")
                return
            tasks = store.claim_analysis_tasks(
                local_worker_id,
                ["discogs-effnet-heads"],
                limit=1,
                lease_seconds=3600,
            )
            if not tasks:
                durable_job = store.get_analysis_job(job_id)
                if durable_job is None or durable_job.status not in ACTIVE_JOB_STATUSES:
                    break
                update_job(
                    job_id,
                    done=durable_job.done,
                    failed=durable_job.failed,
                    message=durable_job.message,
                    **analyze_progress(started_at, total, durable_job.done, durable_job.failed),
                )
                time.sleep(2)
                continue
            task = tasks[0]
            track = store.get_track(task.track_id)
            if track is None:
                store.fail_analysis_task(
                    task.id,
                    error="Track not found",
                    error_type="ValueError",
                    stage="load_track",
                    worker_id=local_worker_id,
                    retryable=False,
                )
                continue
            update_job(job_id, current=track.path, message=f"Analyzing heads for {track.path}")
            result = _extract_heads_local(analyzer, store, settings, track)
            if result.status == "ok" and result.outputs is not None:
                try:
                    for output in result.outputs:
                        store.save_model_output(
                            result.track_id,
                            output.model_name,
                            output.scores,
                            output.aggregation,
                        )
                        store.save_predictions(
                            result.track_id,
                            output.model_name,
                            output.predictions,
                        )
                    store.mark_track_available(result.track_id)
                    store.complete_analysis_task(task.id, local_worker_id)
                except Exception as exc:
                    store.fail_analysis_task(
                        task.id,
                        error=str(exc),
                        error_type=type(exc).__name__,
                        stage="save",
                        worker_id=local_worker_id,
                        retryable=False,
                    )
                    analysis_logger.exception(
                        "Head analysis save failed job_id=%s track_id=%s path=%s stage=save",
                        job_id,
                        result.track_id,
                        result.path,
                    )
            else:
                mark_missing_after_failure(store, result)
                store.fail_analysis_task(
                    task.id,
                    error=result.error or "Head analysis failed",
                    error_type=result.error_type or "HeadAnalyzeError",
                    stage=result.stage or "predict",
                    worker_id=local_worker_id,
                    retryable=result.error_type != "FileNotFoundError",
                )
                analysis_logger.error(
                    "Head analysis track failed job_id=%s track_id=%s path=%s stage=%s error_type=%s error=%s\n%s",
                    job_id,
                    result.track_id,
                    result.path,
                    result.stage,
                    result.error_type,
                    result.error,
                    result.traceback or "",
                )
            durable_job = store.get_analysis_job(job_id)
            if durable_job is None:
                continue
            done = durable_job.done
            failed = durable_job.failed
            update_job(
                job_id,
                done=done,
                failed=failed,
                current=result.path,
                message=durable_job.message,
                **analyze_progress(started_at, total, done, failed),
            )
            if (done + failed) % 25 == 0 or done + failed == total:
                progress = analyze_progress(started_at, total, done, failed)
                analysis_logger.info(
                    "Analyze-heads progress job_id=%s done=%s failed=%s total=%s elapsed=%.1f tracks_per_min=%s eta_seconds=%s",
                    job_id,
                    done,
                    failed,
                    total,
                    progress["elapsed_seconds"],
                    progress["tracks_per_min"],
                    progress["eta_seconds"],
                )
        analysis_logger.info(
            "Finished analyze-heads job job_id=%s done=%s failed=%s total=%s",
            job_id,
            done,
            failed,
            total,
        )
        finish_job(job_id, "completed", f"Analyzed heads for {done} tracks, failed {failed}")
    except Exception as exc:
        analysis_logger.exception("Analyze-heads job failed job_id=%s", job_id)
        finish_job(job_id, "failed", str(exc))


def _analyze_genres_job(job_id: str, _model: str, limit: int | None) -> None:
    _analyze_heads_job(job_id, limit)


def _download_head_models_job(job_id: str) -> None:
    try:
        _store, settings = context()
        files = required_model_files()
        total = len(files)
        logger.info("Starting download head models job job_id=%s files=%s", job_id, total)
        update_job(job_id, status="running", total=total, message="Downloading head models")
        downloaded = 0
        ready = 0
        for filename, source_url in files:
            update_job(
                job_id,
                current=filename,
                message=f"Checking {filename}",
            )
            try:
                result = download_model_file(settings, filename, source_url)
            except Exception as exc:
                logger.exception("Download head model failed job_id=%s filename=%s url=%s", job_id, filename, source_url)
                detail = exception_detail(exc)
                hint = download_failure_hint(exc)
                lines = [f"File: {filename}", f"URL: {source_url}", detail]
                if hint:
                    lines.append(f"Hint: {hint}")
                lines.append(exception_traceback(exc))
                finish_job(
                    job_id,
                    "failed",
                    f"Failed to download {filename}: {hint or detail.splitlines()[0]}",
                    error_detail="\n".join(lines),
                )
                return
            if result.downloaded:
                downloaded += 1
            ready += 1
            update_job(
                job_id,
                done=ready,
                current=str(result.path),
                message=f"Ready {ready}/{total} head model files, downloaded {downloaded}",
            )
        finish_job(
            job_id,
            "completed",
            f"Head model files ready: {ready}/{total}, downloaded {downloaded}",
        )
    except Exception as exc:
        logger.exception("Download head models job failed job_id=%s", job_id)
        detail = exception_detail(exc)
        finish_job(job_id, "failed", detail.splitlines()[0], error_detail=exception_traceback(exc))


def _extract_audio_features_local(
    analyzer: AudioFeatureAnalyzer,
    store: Store,
    settings: Settings,
    track: Track,
) -> AudioFeaturesResult:
    audio_path, manager, failure = _prepare_analyze_audio_path(store, settings, track)
    if failure is not None:
        return AudioFeaturesResult(
            track_id=track.id,
            path=track.path,
            status="failed",
            error=failure.error,
            error_type=failure.error_type,
            traceback=failure.traceback,
            stage=failure.stage,
        )
    try:
        features = analyzer.analyze_track(audio_path)
        return AudioFeaturesResult(
            track_id=track.id,
            path=track.path,
            status="ok",
            features=features,
        )
    except Exception as exc:
        return AudioFeaturesResult(
            track_id=track.id,
            path=track.path,
            status="failed",
            **analyze_failure_fields(exc, "audio_features"),
        )
    finally:
        _cleanup_audio_manager(manager)


def _iter_audio_feature_task_results(
    tasks: list[AnalysisTask],
    store: Store,
    settings: Settings,
    workers: int,
):
    if SHUTDOWN_REQUESTED:
        return
    if workers <= 1:
        analyzer = AudioFeatureAnalyzer()
        for task in tasks:
            if SHUTDOWN_REQUESTED:
                return
            result = _extract_audio_features_local(analyzer, store, settings, task_to_track(task))
            yield task, result
        return

    executor = ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_audio_feature_worker,
        mp_context=multiprocessing.get_context("spawn"),
    )
    register_analyze_executor(executor)
    audio_managers: list[object] = []
    try:
        future_to_task = {}
        for task in tasks:
            track = task_to_track(task)
            audio_path, manager, failure = _prepare_analyze_audio_path(store, settings, track)
            if failure is not None:
                yield task, AudioFeaturesResult(
                    track_id=track.id,
                    path=track.path,
                    status="failed",
                    error=failure.error,
                    error_type=failure.error_type,
                    traceback=failure.traceback,
                    stage=failure.stage,
                )
                continue
            audio_managers.append(manager)
            future = executor.submit(_extract_audio_features_worker, task.track_id, str(audio_path))
            future_to_task[future] = task
        for future in as_completed(future_to_task):
            if SHUTDOWN_REQUESTED:
                break
            task = future_to_task[future]
            try:
                yield task, future.result()
            except Exception as exc:
                yield task, AudioFeaturesResult(
                    track_id=task.track_id,
                    path=task.path,
                    status="failed",
                    **analyze_failure_fields(exc, "audio_features"),
                )
    finally:
        for manager in audio_managers:
            _cleanup_audio_manager(manager)
        unregister_analyze_executor(executor)
        if SHUTDOWN_REQUESTED:
            terminate_process_pool(executor)
        else:
            try:
                executor.shutdown(wait=True, cancel_futures=False)
            except Exception:
                pass


def _analyze_audio_features_job(
    job_id: str,
    limit: int | None,
    workers: int = 1,
    local_executor_enabled: bool = True,
    max_attempts: int = 3,
    enqueue: bool = True,
) -> None:
    try:
        if SHUTDOWN_REQUESTED:
            finish_job(job_id, "failed", "Audio feature analysis cancelled during application shutdown")
            return
        store, settings = context()
        if enqueue:
            tracks = store.list_tracks_missing_features(AUDIO_FEATURE_EXTRACTOR, limit=limit)
            durable_job = store.create_analysis_job(
                AUDIO_FEATURE_EXTRACTOR,
                limit,
                kind="analyze-audio-features",
                tracks=tracks,
                local_executor_enabled=local_executor_enabled,
                max_attempts=max_attempts,
                job_id=job_id,
            )
        else:
            durable_job = store.get_analysis_job(job_id)
            if durable_job is None:
                raise ValueError(f"Audio feature analysis job not found: {job_id}")
        total = durable_job.total
        workers = max(1, min(int(workers), MAX_AUDIO_FEATURE_WORKERS))
        started_at = perf_counter()
        analysis_logger.info(
            "Starting analyze-audio-features job job_id=%s limit=%s total=%s extractor=%s workers=%s",
            job_id,
            limit,
            total,
            AUDIO_FEATURE_EXTRACTOR,
            workers,
        )
        update_job(
            job_id,
            status="running",
            total=total,
            message=f"Analyzing audio features for {total} tracks",
            **analyze_progress(started_at, total, 0, 0),
        )
        done = 0
        failed = 0
        if not local_executor_enabled or total == 0:
            if total == 0:
                finish_job(job_id, "completed", "Analyzed audio features for 0 tracks, failed 0")
            else:
                update_job(job_id, status="running", message=f"Queued {total} audio feature tasks for remote workers")
            return
        local_worker_id = f"local-{job_id}"
        local_claim_batch_size = max(workers * 4, 16)
        while True:
            if SHUTDOWN_REQUESTED:
                finish_job(job_id, "failed", "Audio feature analysis cancelled during application shutdown")
                return
            tasks = store.claim_analysis_tasks(
                local_worker_id,
                [AUDIO_FEATURE_EXTRACTOR],
                limit=local_claim_batch_size,
                lease_seconds=3600,
            )
            if not tasks:
                durable_job = store.get_analysis_job(job_id)
                if durable_job is None or durable_job.status not in ACTIVE_JOB_STATUSES:
                    break
                update_job(
                    job_id,
                    done=durable_job.done,
                    failed=durable_job.failed,
                    message=durable_job.message,
                    **analyze_progress(started_at, total, durable_job.done, durable_job.failed),
                )
                time.sleep(2)
                continue
            for task, result in _iter_audio_feature_task_results(tasks, store, settings, workers):
                update_job(job_id, current=result.path, message=f"Analyzing audio features for {result.path}")
                if result.status == "ok" and result.features is not None:
                    try:
                        store.save_features(result.track_id, result.features)
                        store.mark_track_available(result.track_id)
                        store.complete_analysis_task(task.id, local_worker_id)
                    except Exception as exc:
                        store.fail_analysis_task(
                            task.id,
                            error=str(exc),
                            error_type=type(exc).__name__,
                            stage="save",
                            worker_id=local_worker_id,
                            retryable=False,
                        )
                        analysis_logger.exception(
                            "Audio feature save failed job_id=%s track_id=%s path=%s extractor=%s stage=save",
                            job_id,
                            result.track_id,
                            result.path,
                            AUDIO_FEATURE_EXTRACTOR,
                        )
                else:
                    mark_missing_after_failure(store, result)
                    store.fail_analysis_task(
                        task.id,
                        error=result.error or "Audio feature analysis failed",
                        error_type=result.error_type or "AudioFeatureError",
                        stage=result.stage or "extract",
                        worker_id=local_worker_id,
                        retryable=result.error_type != "FileNotFoundError",
                    )
                    analysis_logger.error(
                        "Audio feature track failed job_id=%s track_id=%s path=%s extractor=%s stage=%s error_type=%s error=%s\n%s",
                        job_id,
                        result.track_id,
                        result.path,
                        AUDIO_FEATURE_EXTRACTOR,
                        result.stage,
                        result.error_type,
                        result.error,
                        result.traceback or "",
                    )

                durable_job = store.get_analysis_job(job_id)
                if durable_job is None:
                    continue
                done = durable_job.done
                failed = durable_job.failed
                update_job(
                    job_id,
                    done=done,
                    failed=failed,
                    current=result.path,
                    message=durable_job.message,
                    **analyze_progress(started_at, total, done, failed),
                )
                if (done + failed) % 25 == 0 or done + failed == total:
                    progress = analyze_progress(started_at, total, done, failed)
                    analysis_logger.info(
                        "Analyze-audio-features progress job_id=%s done=%s failed=%s total=%s elapsed=%.1f tracks_per_min=%s eta_seconds=%s",
                        job_id,
                        done,
                        failed,
                        total,
                        progress["elapsed_seconds"],
                        progress["tracks_per_min"],
                        progress["eta_seconds"],
                    )
        analysis_logger.info(
            "Finished analyze-audio-features job job_id=%s done=%s failed=%s total=%s",
            job_id,
            done,
            failed,
            total,
        )
        finish_job(job_id, "completed", f"Analyzed audio features for {done} tracks, failed {failed}")
    except Exception as exc:
        analysis_logger.exception("Analyze-audio-features job failed job_id=%s", job_id)
        finish_job(job_id, "failed", str(exc))


def _index_job(job_id: str, model: str) -> None:
    try:
        store, settings = context()
        logger.info("Starting index job job_id=%s model=%s", job_id, model)
        update_job(job_id, status="running", message=f"Building index for {model}")
        path = build_index(store, settings, model)
        update_job(job_id, done=1, total=1)
        finish_job(job_id, "completed", f"Built {path}")
    except Exception as exc:
        logger.exception("Index job failed job_id=%s model=%s", job_id, model)
        finish_job(job_id, "failed", str(exc))


UI_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>discocs</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #101214;
      --panel: #171b1f;
      --panel-2: #20262b;
      --text: #eef2f3;
      --muted: #aeb8bc;
      --line: #30383f;
      --accent: #59c3a6;
      --accent-2: #e0b15a;
      --bad: #e27373;
      --blue: #7aa7ff;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; height: 100vh; min-height: 100vh; display: flex; flex-direction: column;
      overflow: hidden; background: var(--bg); color: var(--text);
    }
    button, input, select { font: inherit; }
    button {
      min-height: 36px; border: 1px solid var(--line); border-radius: 6px; padding: 0 12px;
      background: var(--panel-2); color: var(--text); cursor: pointer;
    }
    button.primary { background: var(--accent); border-color: var(--accent); color: #07110e; font-weight: 700; }
    button:disabled { opacity: .55; cursor: not-allowed; }
    input, select {
      min-height: 36px; border: 1px solid var(--line); border-radius: 6px; padding: 0 10px;
      background: #0d0f11; color: var(--text);
    }
    .app { display: grid; grid-template-columns: 220px minmax(0, 1fr); flex: 1; min-height: 0; }
    aside { border-right: 1px solid var(--line); background: #111518; padding: 18px; overflow-y: auto; min-height: 0; }
    main {
      padding: 18px; display: flex; flex-direction: column; gap: 16px;
      min-height: 0; overflow-y: auto;
    }
    h1 { font-size: 22px; margin: 0 0 16px; letter-spacing: 0; }
    h2 { font-size: 16px; margin: 0 0 10px; letter-spacing: 0; }
    label { display: grid; gap: 6px; color: var(--muted); font-size: 13px; margin: 10px 0; }
    .label-title { display: inline-flex; align-items: center; gap: 6px; min-width: 0; }
    .info {
      position: relative; display: inline-flex; align-items: center; justify-content: center;
      width: 18px; height: 18px; border: 1px solid var(--line); border-radius: 999px;
      color: var(--muted); background: var(--panel-2); font-size: 12px; font-weight: 700;
      cursor: help; flex: 0 0 auto;
    }
    .info::after {
      content: attr(data-tooltip); position: absolute; left: 50%; bottom: calc(100% + 8px);
      transform: translateX(-50%); width: min(280px, 78vw); padding: 8px 10px;
      border: 1px solid var(--line); border-radius: 6px; background: #0b0d0f;
      color: var(--text); font-size: 12px; font-weight: 400; line-height: 1.35;
      opacity: 0; pointer-events: none; transition: opacity .12s ease; z-index: 10;
      box-shadow: 0 8px 24px rgba(0,0,0,.35);
    }
    .info:focus { outline: 2px solid var(--accent); outline-offset: 2px; }
    .info:hover::after, .info:focus::after { opacity: 1; }
    nav { display: grid; gap: 8px; }
    nav button { justify-content: flex-start; text-align: left; }
    nav button.active { border-color: var(--accent); color: var(--accent); }
    .section { display: none; }
    .section.active { display: grid; gap: 16px; }
    .section.section-fill.active {
      display: flex; flex-direction: column; flex: 1; min-height: 0; overflow: hidden; gap: 16px;
    }
    .section-fill.active > .layout,
    .section-fill.active > .browse-layout,
    .section-fill.active > .instant-mix-layout,
    .section-fill.active > .metrics-layout { flex: 1; min-height: 0; align-items: stretch; }
    .section-fill.active > .panel.panel-fill { flex: 1; min-height: 0; }
    .actions { display: flex; gap: 8px; flex-wrap: wrap; }
    .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 10px; }
    .stat {
      border: 1px solid var(--line); border-radius: 6px; padding: 12px; background: var(--panel);
      display: grid; grid-template-rows: auto auto 1fr auto; gap: 8px; min-width: 0;
    }
    .stat strong { display: block; font-size: 24px; line-height: 1.05; }
    .stat span { color: var(--muted); font-size: 12px; }
    .stat h3 { margin: 0; font-size: 13px; overflow-wrap: anywhere; min-height: 18px; }
    .stat-count { display: grid; gap: 2px; }
    .stat-lines { display: grid; gap: 4px; align-content: start; }
    .stat-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-height: 36px; }
    .stat-actions button { width: fit-content; }
    .stat-index-row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .stat-icon-button {
      min-height: 26px; width: 28px; padding: 0; display: inline-flex; align-items: center; justify-content: center;
      border-radius: 6px; line-height: 1; font-size: 16px;
    }
    .model-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 10px; }
    .model-card.selected { border-color: var(--accent); }
    .status-ready { border: 1px solid var(--accent); color: var(--accent); background: transparent; }
    .status-stale, .status-unknown { border: 1px solid var(--accent-2); color: var(--accent-2); background: transparent; }
    .status-missing, .status-failed { border: 1px solid var(--bad); color: var(--bad); background: transparent; }
    .index-status-ready { color: var(--accent); }
    .index-status-stale, .index-status-unknown { color: var(--accent-2); }
    .index-status-missing, .index-status-failed { color: var(--bad); }
    .layout {
      display: grid; grid-template-columns: minmax(320px, .9fr) minmax(360px, 1.1fr);
      gap: 16px; min-height: 0;
    }
    .instant-mix-layout {
      display: grid; grid-template-columns: minmax(280px, 360px) minmax(460px, 1fr);
      gap: 16px; min-height: 0;
    }
    .instant-mix-sidebar { display: flex; flex-direction: column; gap: 16px; min-height: 0; }
    .instant-mix-controls { flex: 0 0 auto; }
    .instant-mix-history { flex: 1; min-height: 0; }
    .browse-layout {
      display: grid; grid-template-columns: minmax(240px, .55fr) minmax(420px, 1.45fr);
      gap: 16px; min-height: 0;
    }
    .panel-fill { display: flex; flex-direction: column; min-height: 0; }
    .list-region, .table-region, .facet-scroll {
      flex: 1; min-height: 0; overflow-y: auto; padding-right: 4px; padding-bottom: 8px;
    }
    .blend-status { flex-shrink: 0; }
    .facet-group { display: grid; gap: 6px; margin-bottom: 12px; }
    .facet-list { display: grid; gap: 6px; max-height: 164px; overflow: auto; padding-right: 4px; }
    .facet-button { justify-content: space-between; text-align: left; width: 100%; min-height: 32px; overflow: hidden; }
    .facet-button.active { border-color: var(--accent); color: var(--accent); }
    .facet-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .basket { display: grid; gap: 8px; margin-top: 10px; }
    .rating-active { border-color: var(--accent); color: var(--accent); }
    .like-active { border-color: var(--accent); color: var(--accent); }
    .navidrome-like-button { display: inline-flex; align-items: center; justify-content: center; min-width: 42px; }
    .bi { width: 1em; height: 1em; fill: currentColor; flex: 0 0 auto; }
    .panel { border: 1px solid var(--line); border-radius: 8px; background: var(--panel); padding: 14px; min-width: 0; }
    .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .search { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; margin-bottom: 10px; }
    .list { display: grid; gap: 8px; align-content: start; }
    .track {
      display: grid; gap: 4px; border: 1px solid var(--line); border-radius: 6px; padding: 10px;
      background: #14181b;
    }
    .track:has(.track-body) {
      grid-template-columns: 112px minmax(0, 1fr); grid-template-rows: auto auto;
      column-gap: 12px; row-gap: 6px;
    }
    .track-body { display: contents; }
    .track-main { grid-column: 2; grid-row: 1; min-width: 0; display: grid; gap: 4px; }
    .track-head { min-width: 0; }
    .track-title-row { display: flex; align-items: baseline; gap: 6px; min-width: 0; flex-wrap: wrap; }
    .track-title-main { min-width: 0; overflow-wrap: anywhere; }
    .track-id { color: var(--muted); }
    .track-score-inline { color: var(--accent-2); font-weight: 700; }
    .track-card-line { display: flex; gap: 12px; justify-content: space-between; align-items: baseline; min-width: 0; }
    .track-card-left { min-width: 0; overflow-wrap: anywhere; }
    .track-card-right { flex: 0 0 auto; text-align: right; white-space: nowrap; }
    .track-actions {
      display: flex; gap: 8px; flex-wrap: wrap; justify-content: space-between;
      grid-column: 2; grid-row: 2; align-items: center; margin-top: 0;
    }
    .track-action-group { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .track-command-group { justify-content: flex-end; margin-left: auto; }
    .cover {
      grid-column: 1; grid-row: 1 / span 2; align-self: stretch;
      width: 112px; min-height: 112px; border: 1px solid var(--line); border-radius: 6px; overflow: hidden;
      background: linear-gradient(135deg, #20272b, #111518); display: grid; place-items: center; color: var(--muted);
      font-size: 11px; font-weight: 700; position: relative;
    }
    .cover::before { content: "ART"; }
    .cover img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; display: block; }
    .cover.empty img { display: none; }
    .cover.empty::before { content: "ART"; }
    .track.selected { border-color: var(--accent); }
    .title { font-weight: 700; overflow-wrap: anywhere; }
    .meta { color: var(--muted); font-size: 13px; overflow-wrap: anywhere; }
    .path { color: #829096; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .model-table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }
    .model-table th, .model-table td { border-top: 1px solid var(--line); padding: 8px; text-align: left; vertical-align: top; }
    .model-table th { color: var(--muted); font-weight: 600; }
    #erroredFiles .table-region { overflow: auto; }
    .error-table { min-width: 1180px; table-layout: fixed; }
    .error-table .check-col { width: 32px; }
    .error-table .track-col { width: 220px; }
    .error-table .path-col { width: 42%; }
    .error-table .model-col { width: 170px; }
    .error-table .error-col { width: 300px; }
    .error-table .updated-col { width: 150px; }
    .error-table .track-cell, .error-table .model-cell, .error-table .updated-cell { overflow-wrap: anywhere; }
    .error-table .path-cell { max-width: 0; }
    .error-table .error-text {
      white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; max-height: 180px; overflow: auto;
    }
    .score { color: var(--accent-2); font-weight: 700; }
    .jobs { display: grid; gap: 8px; }
    .job { border: 1px solid var(--line); border-radius: 6px; padding: 10px; background: #14181b; }
    .job pre { white-space: pre-wrap; overflow-wrap: anywhere; margin: 6px 0 0; }
    .bar { height: 6px; background: #0b0d0f; border-radius: 999px; overflow: hidden; margin-top: 8px; }
    .fill { height: 100%; background: var(--accent); width: 0%; }
    .status-deferred .fill { background: var(--accent-2); }
    .status-failed .fill { background: var(--bad); }
    .pill { display: inline-flex; align-items: center; min-height: 24px; padding: 0 8px; border-radius: 999px; background: var(--panel-2); color: var(--muted); font-size: 12px; }
    .bad-pill { border: 1px solid var(--bad); color: var(--bad); background: transparent; }
    .player {
      flex-shrink: 0; border-top: 1px solid var(--line); background: #0f1316;
      padding: 12px 18px; display: grid; gap: 8px;
    }
    .navidrome-debug {
      color: var(--muted); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .build-marker { color: var(--accent-2); font-size: 12px; font-weight: 700; }
    audio { width: 100%; }
    .error { color: var(--bad); min-height: 20px; }
    .active-track { border-color: var(--blue); }
    .icon-button { width: 36px; padding: 0; display: inline-flex; align-items: center; justify-content: center; }
    .icon-tablet {
      width: 16px; height: 20px; border: 2px solid currentColor; border-radius: 3px; position: relative;
      display: inline-block;
    }
    .icon-tablet::after {
      content: ""; position: absolute; left: 50%; bottom: 2px; width: 4px; height: 2px;
      transform: translateX(-50%); border-radius: 999px; background: currentColor;
    }
    .modal-backdrop {
      position: fixed; inset: 0; z-index: 30; display: none; align-items: center; justify-content: center;
      padding: 20px; background: rgba(0,0,0,.68);
    }
    .modal-backdrop.open { display: flex; }
    .modal {
      width: min(980px, 96vw); max-height: 88vh; overflow: auto; border: 1px solid var(--line);
      border-radius: 8px; background: var(--panel); box-shadow: 0 20px 60px rgba(0,0,0,.45);
      padding: 14px;
    }
    .analysis-grid { display: grid; gap: 10px; margin-top: 12px; }
    .analysis-output { border-top: 1px solid var(--line); padding-top: 10px; display: grid; gap: 8px; }
    .tag-list { display: flex; gap: 6px; flex-wrap: wrap; }
    .score-list {
      max-height: 130px; overflow: auto; margin: 0; padding: 8px; border: 1px solid var(--line);
      border-radius: 6px; background: #0d0f11; color: var(--muted); font-size: 12px;
    }
    @media (max-width: 920px) {
      .app { grid-template-columns: 1fr; }
      aside { border-right: 0; border-bottom: 1px solid var(--line); }
      .stats, .layout, .browse-layout, .instant-mix-layout { grid-template-columns: 1fr; }
      .section-fill.active > .layout,
      .section-fill.active > .browse-layout,
      .section-fill.active > .instant-mix-layout {
        grid-template-rows: minmax(180px, .9fr) minmax(240px, 1.1fr);
      }
      .track:has(.track-body) { grid-template-columns: 80px minmax(0, 1fr); }
      .cover { width: 80px; min-height: 80px; }
      .track-card-line { display: block; }
      .track-card-right { text-align: left; white-space: normal; }
    }
    .metrics-layout { display:grid; grid-template-columns:minmax(260px, 340px) minmax(0, 1fr); gap:16px; height:100%; min-height:0; }
    .metrics-controls { flex-shrink:0; }
    .metric-filter-scroll { flex:1; min-height:0; overflow-y:auto; padding-right:4px; padding-bottom:8px; margin-top:8px; }
    .metric-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(160px, 1fr)); gap:8px; margin-bottom:12px; }
    .metric-card { border:1px solid var(--line); border-radius:8px; padding:10px; background:var(--panel-2); cursor:pointer; }
    .metric-card.active { border-color:var(--accent); box-shadow:0 0 0 1px var(--accent); }
    .metric-card strong { display:block; margin-bottom:4px; }
    .metric-filter { border-top:1px solid var(--line); padding-top:10px; margin-top:10px; }
    .metric-filter:first-child { border-top:0; padding-top:0; margin-top:0; }
    .metric-value-list { display:grid; gap:6px; margin-top:8px; max-height:240px; overflow:auto; padding-right:4px; }
    .metric-value-list label { margin:0; display:grid; grid-template-columns:18px minmax(0, 1fr); align-items:center; gap:6px; font-size:12px; }
    .metric-value-list span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .range-pair { display:grid; gap:8px; margin-top:8px; min-width:0; }
    .range-values { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
    .range-values input { min-height:34px; min-width:0; }
    .range-slider { position:relative; height:30px; margin:0 2px; }
    .range-slider .range-track { position:absolute; left:0; right:0; top:13px; height:4px; border-radius:999px; background:var(--line); }
    .range-slider input[type="range"] {
      position:absolute; left:0; top:0; width:100%; min-height:30px; padding:0; margin:0;
      background:transparent; pointer-events:none; -webkit-appearance:none; appearance:none;
    }
    .range-slider input[type="range"]::-webkit-slider-thumb { pointer-events:auto; -webkit-appearance:none; appearance:none; width:16px; height:16px; border-radius:50%; background:var(--accent); border:0; }
    .range-slider input[type="range"]::-moz-range-thumb { pointer-events:auto; width:16px; height:16px; border-radius:50%; background:var(--accent); border:0; }
    .range-slider input[type="range"]::-webkit-slider-runnable-track { background:transparent; }
    .range-slider input[type="range"]::-moz-range-track { background:transparent; }
    .feature-chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
    .feature-chip { border:1px solid var(--line); border-radius:999px; padding:3px 8px; font-size:12px; color:var(--muted); }
    @media (max-width: 1100px) { .metrics-layout { grid-template-columns:minmax(320px, .75fr) minmax(360px, 1.25fr); } }
    @media (max-width: 900px) {
      .metrics-layout { grid-template-columns:1fr; overflow:auto; }
      .metrics-layout > .panel { min-height:420px; }
      .metric-filter-scroll { max-height:none; }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside>
      <h1>discocs</h1>
      <nav>
        <button class="active" data-nav="dashboard" onclick="showSection('dashboard')">Dashboard</button>
        <button data-nav="library" onclick="showSection('library')">Library</button>
        <button data-nav="browse" onclick="showSection('browse')">Browse</button>
        <button data-nav="metrics" onclick="showSection('metrics')">Metrics</button>
        <button data-nav="lostFiles" onclick="showSection('lostFiles')">Lost files</button>
        <button data-nav="erroredFiles" onclick="showSection('erroredFiles')">Errored files</button>
        <button data-nav="recommendations" onclick="showSection('recommendations')">Recommendations</button>
        <button data-nav="textSearch" onclick="showSection('textSearch')">Text search</button>
        <button data-nav="navidromeLikes" onclick="showSection('navidromeLikes')">Navidrome likes</button>
        <button data-nav="instantMix" onclick="showSection('instantMix')">Instant mix</button>
        <button data-nav="evaluation" onclick="showSection('evaluation')">Evaluation</button>
        <button data-nav="jobs" onclick="showSection('jobsPage')">Jobs</button>
        <button data-nav="workers" onclick="showSection('workersPage')">Workers</button>
        <button data-nav="settings" onclick="showSection('settings')">Settings</button>
      </nav>
    </aside>
    <main>
      <section id="dashboard" class="section active">
        <div class="stats" id="dashboardCards">
          <div class="stat"><strong id="tracks">0</strong><span>tracks</span></div>
          <div class="stat"><strong id="missingHeadPackTracks">0</strong><span>need tags</span></div>
          <div class="stat"><strong id="missingAudioFeatures">0</strong><span>need audio features</span></div>
          <div class="stat"><strong id="missingFiles">0</strong><span>lost files</span></div>
          <div class="stat"><strong id="erroredFilesStat">0</strong><span>errored files</span></div>
          <div class="stat"><strong id="navidromeExternalTracks">0</strong><span>Navidrome mapped</span></div>
        </div>
        <div class="model-cards" id="modelCards"></div>
        <div class="panel">
          <h2>Pipeline</h2>
          <div class="meta" id="modelState"></div>
        </div>
        <details class="panel" id="headPackDetails">
          <summary id="headPackSummary">Head models</summary>
          <div class="actions" style="margin-top:12px">
            <button id="downloadHeadsBtn" onclick="downloadHeadModels()">Download head models</button>
          </div>
          <div class="meta" id="headPackReadiness"></div>
          <div id="headPackModelTable"></div>
        </details>
        <div class="panel">
          <h2>Recent jobs</h2>
          <div id="dashboardJobs" class="jobs"></div>
        </div>
      </section>
      <section id="library" class="section section-fill">
        <div class="panel panel-fill">
          <h2>Library</h2>
          <div class="search">
            <input id="query" placeholder="Search artist, title, album, path">
            <button onclick="searchTracks()">Search</button>
          </div>
          <div class="row">
            <label style="margin:0">Embedding status
              <select id="embeddingStatus" onchange="searchTracks()">
                <option value="all">all</option>
                <option value="ready">ready</option>
                <option value="missing">missing</option>
              </select>
            </label>
          </div>
          <div class="list-region"><div id="tracksList" class="list"></div></div>
        </div>
      </section>
      <section id="browse" class="section section-fill">
        <div class="browse-layout">
          <div class="panel panel-fill">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <h2>Browse</h2>
              <button onclick="clearBrowseFilters()">Clear</button>
            </div>
            <label style="margin-top:0">Embedding status
              <select id="browseEmbeddingStatus" onchange="refreshBrowse()">
                <option value="all">all</option>
                <option value="ready">ready</option>
                <option value="missing">missing</option>
              </select>
            </label>
            <div class="facet-scroll">
              <div class="facet-group">
                <strong>Genres</strong>
                <div id="genreFacets" class="facet-list"></div>
              </div>
              <div class="facet-group">
                <strong>Years</strong>
                <div id="yearFacets" class="facet-list"></div>
              </div>
              <div class="facet-group">
                <strong>Artists</strong>
                <div id="artistFacets" class="facet-list"></div>
              </div>
              <div class="facet-group">
                <strong>Albums</strong>
                <div id="albumFacets" class="facet-list"></div>
              </div>
              <div class="facet-group">
                <strong>Folders</strong>
                <div id="folderFacets" class="facet-list"></div>
              </div>
            </div>
          </div>
          <div class="panel panel-fill">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <h2>Tracks</h2>
              <span class="pill" id="browseFilterLabel">all tracks</span>
            </div>
            <div class="search">
              <input id="browseQuery" placeholder="Search within selected folder or tag">
              <button onclick="loadBrowseTracks()">Search</button>
            </div>
            <div class="list-region"><div id="browseTracks" class="list"></div></div>
          </div>
        </div>
      </section>
      <section id="metrics" class="section section-fill">
        <div class="metrics-layout">
          <div class="panel panel-fill">
            <div class="metrics-controls">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <h2>Metrics</h2>
              <button onclick="clearMetricFilters()">Clear</button>
            </div>
            <label style="margin-top:0">Source
              <select id="metricsSource" onchange="loadMetricsExplorer()">
                <option value="audio_features">Audio features</option>
                <option value="heads">Discogs-EffNet heads</option>
              </select>
            </label>
            <label style="margin-top:0">Extractor
              <input id="metricsExtractor" value="audio_features_v1" onchange="loadMetricsExplorer()">
            </label>
            <div class="search">
              <input id="metricsQuery" placeholder="Search artist, title, album, path">
              <button onclick="searchMetrics()">Search</button>
            </div>
            <label>Sort by
              <select id="metricsSort" onchange="searchMetrics()"></select>
            </label>
            <label>Limit
              <input id="metricsLimit" type="number" min="1" max="500" value="50" onchange="searchMetrics()">
            </label>
            </div>
            <div id="metricFilterList" class="metric-filter-scroll"></div>
          </div>
          <div class="panel panel-fill">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <h2>Catalog lab</h2>
              <span class="pill" id="metricsResultCount">0 tracks</span>
            </div>
            <div id="metricsSummary" class="metric-grid"></div>
            <div class="list-region"><div id="metricsResults" class="list"></div></div>
          </div>
        </div>
      </section>
      <section id="lostFiles" class="section section-fill">
        <div class="panel panel-fill">
          <div class="row" style="justify-content:space-between; margin-bottom:10px">
            <h2>Lost files</h2>
            <span class="pill" id="lostFilesCount">0 lost</span>
          </div>
          <div class="actions">
            <button id="checkMissingBtn" onclick="checkMissingFiles()">Check missing files</button>
            <button onclick="toggleLostFilesSelection(true)">Select all</button>
            <button onclick="toggleLostFilesSelection(false)">Clear selection</button>
            <button id="deleteLostBtn" onclick="deleteSelectedLostFiles()">Remove selected</button>
            <button id="deleteAllLostBtn" onclick="deleteAllLostFiles()">Remove all</button>
            <button class="primary" onclick="loadLostFiles()">Refresh</button>
          </div>
          <div class="meta" style="margin-top:8px">Missing records stay in the database until you remove them.</div>
          <div class="row" style="justify-content:flex-end; margin-top:10px">
            <button onclick="previousLostFilesPage()">Previous</button>
            <span class="pill" id="lostFilesPage">page 1 / 1</span>
            <button onclick="nextLostFilesPage()">Next</button>
          </div>
          <div class="table-region"><div id="lostFilesList"></div></div>
        </div>
      </section>
      <section id="erroredFiles" class="section section-fill">
        <div class="panel panel-fill">
          <div class="row" style="justify-content:space-between; margin-bottom:10px">
            <h2>Errored files</h2>
            <span class="pill" id="erroredFilesCount">0 errors</span>
          </div>
          <div class="actions">
            <button onclick="toggleErroredFilesSelection(true)">Select all</button>
            <button onclick="toggleErroredFilesSelection(false)">Clear selection</button>
            <button id="deleteErroredBtn" onclick="deleteSelectedErroredFiles()">Remove selected</button>
            <button id="deleteAllErroredBtn" onclick="deleteAllErroredFiles()">Remove all</button>
            <button class="primary" onclick="loadErroredFiles()">Refresh</button>
          </div>
          <div class="meta" style="margin-top:8px">Files that failed analysis, grouped by track and model.</div>
          <div class="row" style="justify-content:flex-end; margin-top:10px">
            <button onclick="previousErroredFilesPage()">Previous</button>
            <span class="pill" id="erroredFilesPage">page 1 / 1</span>
            <button onclick="nextErroredFilesPage()">Next</button>
          </div>
          <div class="table-region"><div id="erroredFilesList"></div></div>
        </div>
      </section>
      <section id="recommendations" class="section section-fill">
        <div class="layout">
          <div class="panel panel-fill">
            <h2>Seed</h2>
            <div id="seedPanel">
              <div class="track">
                <div class="title">No seed selected</div>
                <div class="meta">Pick a seed from Library or search here.</div>
              </div>
            </div>
            <div class="search" style="margin-top:10px">
              <input id="seedQuery" placeholder="Search seed track">
              <button onclick="searchSeeds()">Search</button>
            </div>
            <div class="list-region"><div id="seedResults" class="list"></div></div>
          </div>
          <div class="panel panel-fill">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <h2>Similar</h2>
              <button onclick="refreshSimilarTracks()" id="refreshSimilarBtn" disabled>Refresh similar</button>
            </div>
            <div class="list-region"><div id="similarList" class="list"></div></div>
          </div>
        </div>
      </section>
      <section id="textSearch" class="section section-fill">
        <div class="layout">
          <div class="panel panel-fill">
            <h2>Text search</h2>
            <label style="margin-top:0">Describe music
              <textarea id="textSearchQuery" rows="5" placeholder="deep dub techno with warm chords and no vocals"></textarea>
            </label>
            <div class="actions">
              <button class="primary" onclick="runTextSearch()">Search</button>
              <button onclick="clearTextSearch()">Clear</button>
            </div>
            <div class="row">
              <label style="margin:0">Count
                <input id="textSearchCount" type="number" min="1" max="500" value="50">
              </label>
              <label style="margin:0">Min similarity
                <input id="textSearchMinSimilarity" type="number" min="0" max="1" step="0.001" value="">
              </label>
            </div>
            <label>Max per artist
              <input id="textSearchMaxPerArtist" type="number" min="1" max="100" value="2">
            </label>
            <label class="row">
              <input id="textSearchExcludeSameAlbum" type="checkbox" checked style="min-height:auto">
              <span>Exclude same album</span>
            </label>
            <div class="meta" id="textSearchStatus">Model: muq_mulan</div>
            <div class="actions">
              <button onclick="setTextSearchQuery('ambient dub, spacious, slow')">ambient dub</button>
              <button onclick="setTextSearchQuery('fast electro, metallic drums')">fast electro</button>
              <button onclick="setTextSearchQuery('warm deep house, late night')">deep house</button>
              <button onclick="setTextSearchQuery('breakbeat, melancholic, 90s')">breakbeat</button>
              <button onclick="setTextSearchQuery('dark minimal techno, hypnotic')">minimal techno</button>
            </div>
            <h2 style="margin-top:18px">Recent queries</h2>
            <div id="textSearchRecent" class="list"></div>
          </div>
          <div class="panel panel-fill">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <h2>Results</h2>
              <span class="pill">muq_mulan</span>
            </div>
            <div class="list-region"><div id="textSearchResults" class="list">
              <div class="meta">Describe the music you want to find.</div>
            </div></div>
          </div>
        </div>
      </section>
      <section id="navidromeLikes" class="section section-fill">
        <div class="panel blend-status">
          <div class="row" style="justify-content:space-between; margin-bottom:10px">
            <h2>Navidrome likes</h2>
            <span class="pill">Blend: Average</span>
          </div>
          <div class="meta" id="likedStatusLine">Source: Navidrome starred · not loaded yet</div>
          <div class="meta" id="likedStatusDetail">Load starred tracks for the configured Navidrome user, then refresh recommendations from their average embedding.</div>
          <div class="error" id="likedStatusError"></div>
          <div class="actions">
            <button class="primary" onclick="loadNavidromeLikes()">Load Navidrome likes</button>
            <button onclick="refreshLikedRecommendations()" id="refreshLikedBtn" disabled>Refresh recommendations</button>
            <button onclick="startAnalyze()">Analyze missing</button>
            <button onclick="startNavidromeSync()">Sync catalog</button>
            <button onclick="clearLikedBlend()">Clear</button>
          </div>
        </div>
        <div class="layout">
          <div class="panel panel-fill">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <h2>Liked tracks</h2>
              <label style="margin:0">
                Filter
                <select id="likedFilter" onchange="onLikedFilterChange()">
                  <option value="all">all</option>
                  <option value="ready">ready</option>
                  <option value="missing_embedding">needs embedding</option>
                  <option value="not_synced">not synced</option>
                </select>
              </label>
            </div>
            <div class="search">
              <input id="likedLocalQuery" placeholder="Filter loaded likes">
              <button onclick="renderLikedTracks()">Filter</button>
            </div>
            <div class="list-region"><div id="likedTracksList" class="list"></div></div>
            <details style="margin-top:12px">
              <summary>Add extra track</summary>
              <div class="search" style="margin-top:10px">
                <input id="likedExtraQuery" placeholder="Search library to add">
                <button onclick="searchLikedExtra()">Search</button>
              </div>
              <label class="row" style="margin-top:8px">
                <input id="likedExtraShowMissing" type="checkbox" style="min-height:auto">
                <span>Show tracks without embeddings</span>
              </label>
              <div class="list-region"><div id="likedExtraSearchResults" class="list"></div></div>
              <div class="meta" id="likedExtraSummary" style="margin-top:8px"></div>
            </details>
          </div>
          <div class="panel panel-fill">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <div>
                <h2>Recommendations</h2>
                <div class="meta" id="likedSimilarSubtitle">Recommendations from liked blend</div>
              </div>
            </div>
            <div class="list-region"><div id="likedSimilarList" class="list"></div></div>
          </div>
        </div>
      </section>
      <section id="instantMix" class="section section-fill">
        <div class="instant-mix-layout">
          <div class="instant-mix-sidebar">
            <div class="panel instant-mix-controls">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <h2>Instant mix</h2>
              <button class="primary" onclick="loadInstantMixRequests()">Refresh</button>
            </div>
              <label style="margin-top:0">Model
                <select id="instantMixModel">
                  <option value="discogs_multi">discogs_multi</option>
                  <option value="discogs_track">discogs_track</option>
                  <option value="discogs_release">discogs_release</option>
                  <option value="discogs_label">discogs_label</option>
                  <option value="muq_mulan">muq_mulan</option>
                </select>
              </label>
              <div class="row">
                <label style="margin:0">Count
                  <input id="instantMixCount" type="number" min="1" max="500" value="50">
                </label>
                <label style="margin:0">Min similarity
                  <input id="instantMixMinSimilarity" type="number" min="0" max="1" step="0.001" value="0.5">
                </label>
              </div>
              <label>Max per artist
                <input id="instantMixMaxPerArtist" type="number" min="1" max="100" value="2">
              </label>
              <label class="row">
                <input id="instantMixExcludeSameAlbum" type="checkbox" checked style="min-height:auto">
                <span>Exclude same album</span>
              </label>
              <label class="row">
                <input id="instantMixCountCollaborationArtists" type="checkbox" checked style="min-height:auto">
                <span>Count collaboration artists in artist cap</span>
              </label>
              <div class="actions">
                <button onclick="saveInstantMixSettings()">Save settings</button>
              </div>
              <div class="meta" id="instantMixStatus">Settings are loaded from runtime config.</div>
            </div>
            <div class="panel panel-fill instant-mix-history">
              <h2>Requests</h2>
              <div class="list-region" style="margin-top:12px"><div id="instantMixRequests" class="list"></div></div>
            </div>
          </div>
          <div class="panel panel-fill">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <h2>Request detail</h2>
              <button onclick="backToInstantMixList()">Back</button>
            </div>
            <div class="list-region"><div id="instantMixDetail" class="list">
              <div class="meta">Select a request to inspect returned tracks and parameters.</div>
            </div></div>
          </div>
        </div>
      </section>
      <section id="evaluation" class="section section-fill">
        <div class="layout">
          <div class="panel panel-fill">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <h2>Seed basket</h2>
              <button onclick="clearSeedBasket()">Clear</button>
            </div>
            <div class="row">
              <button class="primary" onclick="startEvaluationSession()" id="startEvaluationBtn">Start session</button>
              <button onclick="nextEvaluationSeed()">Next seed</button>
              <button onclick="skipEvaluationSeed()">Skip seed</button>
            </div>
            <div class="meta" style="margin-top:8px">Ratings are absolute listening judgements for the current seed: good, okay, or bad.</div>
            <div id="evaluationProgress" class="meta" style="margin-top:8px"></div>
            <div class="list-region"><div id="seedBasket" class="basket"></div></div>
          </div>
          <div class="panel panel-fill">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <h2>Evaluate similar</h2>
              <button onclick="refreshSimilarTracks()" id="evaluationRefreshBtn" disabled>Refresh similar</button>
            </div>
            <div id="evaluationSeedPanel"></div>
            <div class="list-region"><div id="evaluationSimilarList" class="list"></div></div>
          </div>
        </div>
      </section>
      <section id="jobsPage" class="section">
        <div class="panel">
          <h2>Jobs</h2>
          <div id="jobs" class="jobs"></div>
        </div>
        <div class="panel">
          <h2>Job details</h2>
          <div id="jobDetail" class="jobs"><div class="meta">Select a job to inspect queued, leased, and failed tasks.</div></div>
        </div>
      </section>
      <section id="workersPage" class="section">
        <div class="panel">
          <h2>Workers</h2>
          <div id="workersSummary" class="stats"></div>
          <div id="workersList" class="jobs"></div>
        </div>
      </section>
      <section id="settings" class="section">
        <div class="panel">
        <h2>Settings</h2>
        <label><span class="label-title">Model <span class="info" tabindex="0" data-tooltip="Embedding model used for analyze, index, and recommendations. Changing it requires separate embeddings and index.">(i)</span></span>
          <select id="model">
            <option value="discogs_multi">discogs_multi</option>
            <option value="discogs_track">discogs_track</option>
            <option value="discogs_release">discogs_release</option>
            <option value="discogs_label">discogs_label</option>
            <option value="muq_mulan">muq_mulan</option>
          </select>
        </label>
        <label><span class="label-title">Analyze limit <span class="info" tabindex="0" data-tooltip="Maximum number of missing tracks to process in one job. Empty means all missing tracks. Smaller batches are easier to test.">(i)</span></span>
          <input id="limit" type="number" min="1" value="20">
        </label>
        <label><span class="label-title">Analyze execution <span class="info" tabindex="0" data-tooltip="Choose where embedding analyze tasks are executed. Remote only queues tasks for HTTP pull workers; local only uses this server; local + remote lets both claim tasks.">(i)</span></span>
          <select id="analyzeExecutionMode" onchange="refreshWorkerCommand()">
            <option value="both">Local + remote</option>
            <option value="remote">Remote only</option>
            <option value="local">Local only</option>
          </select>
        </label>
        <label><span class="label-title">Analyze workers <span class="info" tabindex="0" data-tooltip="Number of analyzer processes. More workers can improve throughput but use more RAM and may slow each individual prediction. Current measured default: 4.">(i)</span></span>
          <input id="workers" type="number" min="1" value="4">
        </label>
        <label><span class="label-title">Analyze TF threads <span class="info" tabindex="0" data-tooltip="TensorFlow/OMP threads per analyzer process. Too high causes contention; benchmarked default is 4 with 4 workers.">(i)</span></span>
          <input id="tfThreads" type="number" min="1" value="4">
        </label>
        <h3>Audio features</h3>
        <label><span class="label-title">Audio feature workers <span class="info" tabindex="0" data-tooltip="Number of local analyzer processes for BPM/key/loudness/dynamics. Separate from Discogs embedding workers so audio feature tuning cannot overload model inference.">(i)</span></span>
          <input id="audioFeatureWorkers" type="number" min="1" value="8">
        </label>
        <label><span class="label-title">Feature extractor <span class="info" tabindex="0" data-tooltip="Audio feature pipeline version stored in track_features. Rescan deletes existing values for this extractor and queues tracks for re-analysis.">(i)</span></span>
          <select id="audioFeaturesExtractor">
            <option value="audio_features_v1">audio_features_v1</option>
          </select>
        </label>
        <div class="meta" id="audioFeaturesStatus">Audio feature status not loaded yet.</div>
        <div class="actions">
          <button id="rescanAudioFeaturesBtn" onclick="rescanAudioFeatures()">Rescan audio features</button>
        </div>
        <div class="meta" id="audioFeaturesRescanStatus"></div>
        <h3>Navidrome</h3>
        <label><span class="label-title">Navidrome URL <span class="info" tabindex="0" data-tooltip="Base URL that this app can reach, for example http://192.168.1.41:4533 or http://navidrome:4533 from Docker.">(i)</span></span>
          <input id="navidromeUrl" placeholder="http://127.0.0.1:4533">
        </label>
        <label><span class="label-title">Navidrome user <span class="info" tabindex="0" data-tooltip="Subsonic/Navidrome username used by this app for sync and audio downloads.">(i)</span></span>
          <input id="navidromeUser" autocomplete="username">
        </label>
        <label><span class="label-title">Navidrome password <span class="info" tabindex="0" data-tooltip="Leave blank when saving to keep the existing saved password.">(i)</span></span>
          <input id="navidromePassword" type="password" autocomplete="current-password" placeholder="leave blank to keep saved password">
        </label>
        <label><span class="label-title">Auth mode</span>
          <select id="navidromeAuthMode">
            <option value="token">token</option>
            <option value="password">password</option>
          </select>
        </label>
        <label><span class="label-title">Timeout seconds</span>
          <input id="navidromeTimeoutSeconds" type="number" min="1" max="600" value="60">
        </label>
        <label><span class="label-title">Download mode</span>
          <select id="navidromeDownloadMode">
            <option value="download">download</option>
            <option value="stream">stream</option>
          </select>
        </label>
        <label><span class="label-title">Temp dir</span>
          <input id="navidromeTempDir" placeholder="data/tmp/navidrome">
        </label>
        <div class="actions">
          <button class="primary" onclick="saveNavidromeSettings()">Save Navidrome</button>
          <button onclick="pingNavidrome()">Ping</button>
        </div>
        <div class="meta" id="navidromeStatus">Navidrome settings are loaded from server config.</div>
        <div class="meta">For Navidrome Instant Mix set <code>ND_AGENTS=discocs-instant-mix,deezer,lastfm,listenbrainz</code>.</div>
        <h3>Remote worker</h3>
        <label><span class="label-title">Server URL for worker <span class="info" tabindex="0" data-tooltip="Base URL that the remote machine can reach. Use the host/IP running this web app, not localhost unless the worker runs on the same machine.">(i)</span></span>
          <input id="workerServerUrl" value="http://127.0.0.1:8711" oninput="refreshWorkerCommand()">
        </label>
        <label><span class="label-title">Worker ID <span class="info" tabindex="0" data-tooltip="Stable name shown in Jobs / Workers. Use a different ID for each remote machine.">(i)</span></span>
          <input id="workerId" value="gpu-4090-1" oninput="refreshWorkerCommand()">
        </label>
        <label><span class="label-title">Claim batch size <span class="info" tabindex="0" data-tooltip="How many queued tasks the worker asks for in one claim. Higher values reduce API round trips.">(i)</span></span>
          <input id="workerClaimBatchSize" type="number" min="1" value="2" oninput="refreshWorkerCommand()">
        </label>
        <label><span class="label-title">Max in-flight tasks <span class="info" tabindex="0" data-tooltip="Maximum leased tasks held by the worker while it downloads and processes audio. Keep this high enough to avoid GPU starvation.">(i)</span></span>
          <input id="workerMaxInflightTasks" type="number" min="1" value="2" oninput="refreshWorkerCommand()">
        </label>
        <label><span class="label-title">Download concurrency <span class="info" tabindex="0" data-tooltip="How many source audio files the worker downloads at the same time. Useful on fast LAN storage.">(i)</span></span>
          <input id="workerDownloadConcurrency" type="number" min="1" value="1" oninput="refreshWorkerCommand()">
        </label>
        <label><span class="label-title">Submit batch size <span class="info" tabindex="0" data-tooltip="How many results/failures the worker sends back in one request.">(i)</span></span>
          <input id="workerSubmitBatchSize" type="number" min="1" value="1" oninput="refreshWorkerCommand()">
        </label>
        <label><span class="label-title">Lease seconds <span class="info" tabindex="0" data-tooltip="How long the server waits before returning an unfinished leased task to the queue. Increase for slow models or long tracks.">(i)</span></span>
          <input id="workerLeaseSeconds" type="number" min="30" value="900" oninput="refreshWorkerCommand()">
        </label>
        <label><span class="label-title">Recycle after tasks <span class="info" tabindex="0" data-tooltip="Optional safety valve for suspected memory leaks. 0 keeps the worker running forever; a positive value exits after that many submitted tasks.">(i)</span></span>
          <input id="workerMaxTasksBeforeExit" type="number" min="0" value="0" oninput="refreshWorkerCommand()">
        </label>
        <label><span class="label-title">Worker command</span>
          <textarea id="workerCommand" rows="5" readonly></textarea>
        </label>
        <label><span class="label-title">Results <span class="info" tabindex="0" data-tooltip="Number of similar tracks requested for the current seed. Higher values return a wider list but may include weaker matches.">(i)</span></span>
          <input id="k" type="number" min="1" max="100" value="30">
        </label>
        <label><span class="label-title">Max per artist <span class="info" tabindex="0" data-tooltip="Limits repeated artists in recommendations. Lower values make results more diverse.">(i)</span></span>
          <input id="maxPerArtist" type="number" min="1" max="20" value="2">
        </label>
        <label class="row">
          <input id="excludeSameAlbum" type="checkbox" checked style="min-height:auto">
          <span class="label-title">Exclude same album <span class="info" tabindex="0" data-tooltip="Removes tracks from the same album as the seed. Useful when you want discovery instead of near-duplicates.">(i)</span></span>
        </label>
        </div>
      </section>
    </main>
  </div>
  <div class="modal-backdrop" id="analysisModal" onclick="closeAnalysisModal(event)">
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="analysisTitle" onclick="event.stopPropagation()">
      <div class="row" style="justify-content:space-between">
        <div>
          <h2 id="analysisTitle">Track analysis</h2>
          <div class="meta" id="analysisSubtitle"></div>
        </div>
        <button class="icon-button" onclick="closeAnalysisModal()" aria-label="Close analysis">&times;</button>
      </div>
      <div id="analysisContent" class="analysis-grid"></div>
    </div>
  </div>
  <div class="player">
    <div class="row" style="justify-content:space-between">
      <strong id="nowPlaying">No track loaded</strong>
      <span class="error" id="playerError"></span>
    </div>
    <div class="navidrome-debug" id="navidromeLikeDebug">
      <span class="build-marker">UI build likes-remote-only-20260611-1918</span>
      · Navidrome likes debug: idle
    </div>
    <audio id="audioPlayer" controls preload="none"></audio>
  </div>
  <script>
    const SETTINGS_KEY = "discocs.settings.v1";
    const BLEND_EXTRA_KEY = "discocs.blendExtra.v1";
    const SETTINGS_FIELDS = [
      "model", "limit", "analyzeExecutionMode", "workers", "tfThreads", "audioFeatureWorkers", "audioFeaturesExtractor",
      "workerServerUrl", "workerId", "workerClaimBatchSize", "workerMaxInflightTasks",
      "workerDownloadConcurrency", "workerSubmitBatchSize", "workerLeaseSeconds",
      "workerMaxTasksBeforeExit",
      "k", "maxPerArtist", "excludeSameAlbum",
      "textSearchCount", "textSearchMinSimilarity", "textSearchMaxPerArtist", "textSearchExcludeSameAlbum"
    ];
    const NAVIDROME_SETTINGS_FIELDS = [
      "navidromeUrl", "navidromeUser", "navidromeAuthMode", "navidromeTimeoutSeconds",
      "navidromeDownloadMode", "navidromeTempDir"
    ];
    let seedId = null;
    let seedTrack = null;
    let activeTrackId = null;
    let currentSimilarTracks = [];
    let lastJobs = [];
    let seedBasket = [];
    let likedCatalog = null;
    let navidromeLikeIdsRefreshScheduled = false;
    let navidromeLikeLastDebug = "idle";
    let extraBlendIds = [];
    let currentInstantMixRequestId = null;
    let textSearchRecentQueries = [];
    let evaluationIndex = -1;
    let browseFilters = {};
    let lostFilesPage = 1;
    const lostFilesPageSize = 50;
    let erroredFilesPage = 1;
    const erroredFilesPageSize = 50;
    let statsInFlight = false;
    let jobsInFlight = false;
    function model() { return document.getElementById("model").value; }
    function text(value) { return value || ""; }
    function formatDuration(seconds) {
      const value = Number(seconds);
      if (!Number.isFinite(value) || value < 0) return "";
      const total = Math.round(value);
      if (total < 60) return `${total}s`;
      const days = Math.floor(total / 86400);
      const hours = Math.floor((total % 86400) / 3600);
      const minutes = Math.floor((total % 3600) / 60);
      const secs = total % 60;
      if (days > 0) return `${days}d ${String(hours).padStart(2, "0")}h`;
      if (hours > 0) return `${hours}h ${String(minutes).padStart(2, "0")}m`;
      return `${minutes}m ${String(secs).padStart(2, "0")}s`;
    }
    function esc(value) {
      return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[char]));
    }
    function label(t) { return `${text(t.artist)} - ${text(t.title) || t.path}`.replace(/^ - /, ""); }
    function encodedArg(value) { return encodeURIComponent(value).replace(/'/g, "%27"); }
    function compactFolder(path) {
      const parts = String(path).split(/[\\/]+/).filter(Boolean);
      if (parts.length <= 2) return path;
      return parts.slice(-2).join(" / ");
    }
    async function json(url, options = {}) {
      const timeoutMs = options.timeoutMs || 30000;
      const timeoutController = new AbortController();
      const timeoutId = setTimeout(() => timeoutController.abort(), timeoutMs);
      const externalSignal = options.signal;
      if (externalSignal) {
        if (externalSignal.aborted) timeoutController.abort();
        else externalSignal.addEventListener("abort", () => timeoutController.abort(), {once: true});
      }
      const fetchOptions = {...options, signal: timeoutController.signal};
      delete fetchOptions.timeoutMs;
      try {
      const response = await fetch(url, fetchOptions);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || response.statusText);
      return data;
      } catch (err) {
        if (err.name === "AbortError") throw new Error("Request timed out or was cancelled");
        throw err;
      } finally {
        clearTimeout(timeoutId);
      }
    }
    function readSavedSettings() {
      try {
        return JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
      } catch (err) {
        return {};
      }
    }
    function saveSettings() {
      const data = {};
      SETTINGS_FIELDS.forEach(id => {
        const element = document.getElementById(id);
        if (!element) return;
        data[id] = element.type === "checkbox" ? element.checked : element.value;
      });
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(data));
    }
    function loadSettings() {
      const data = readSavedSettings();
      SETTINGS_FIELDS.forEach(id => {
        const element = document.getElementById(id);
        if (!element || data[id] === undefined) return;
        if (element.type === "checkbox") {
          element.checked = Boolean(data[id]);
        } else {
          element.value = data[id];
        }
      });
    }
    function bindSettingsAutosave() {
      SETTINGS_FIELDS.forEach(id => {
        const element = document.getElementById(id);
        if (!element) return;
        element.addEventListener("change", saveSettings);
        element.addEventListener("input", saveSettings);
      });
    }
    function workerSetting(id, fallback) {
      const element = document.getElementById(id);
      return element && element.value ? element.value : fallback;
    }
    function shellQuote(value) {
      const textValue = String(value || "");
      return textValue.includes(" ") ? `"${textValue.replace(/"/g, '\\"')}"` : textValue;
    }
    function refreshWorkerCommand() {
      const server = workerSetting("workerServerUrl", "http://127.0.0.1:8711");
      const workerId = workerSetting("workerId", "gpu-4090-1");
      const embeddingModels = "discogs_multi,discogs_track,discogs_release,discogs_label,muq_mulan";
      const command = [
        "recs worker",
        "--server", shellQuote(server),
        "--worker-id", shellQuote(workerId),
        "--models", embeddingModels,
        "--models", "audio_features_v1",
        "--models", "discogs-effnet-heads",
        "--claim-batch-size", workerSetting("workerClaimBatchSize", "2"),
        "--max-inflight-tasks", workerSetting("workerMaxInflightTasks", "2"),
        "--download-concurrency", workerSetting("workerDownloadConcurrency", "1"),
        "--submit-batch-size", workerSetting("workerSubmitBatchSize", "1"),
        "--lease-seconds", workerSetting("workerLeaseSeconds", "900"),
        "--max-tasks-before-exit", workerSetting("workerMaxTasksBeforeExit", "0")
      ].join(" ");
      const target = document.getElementById("workerCommand");
      if (target) target.value = command;
    }
    const VIEW_TO_SECTION = {
      dashboard: "dashboard",
      library: "library",
      browse: "browse",
      metrics: "metrics",
      lostFiles: "lostFiles",
      erroredFiles: "erroredFiles",
      recommendations: "recommendations",
      textSearch: "textSearch",
      navidromeLikes: "navidromeLikes",
      instantMix: "instantMix",
      evaluation: "evaluation",
      jobs: "jobsPage",
      workers: "workersPage",
      settings: "settings",
    };
    const BROWSE_FACET_KEYS = ["folder", "genre", "year", "artist", "album"];
    let metricSummaries = [];
    let metricFilters = {};
    let metricsLoadController = null;
    let metricsSearchController = null;
    let metricsLoadSeq = 0;
    let metricsSearchSeq = 0;
    let applyingRoute = false;

    function sectionIdForView(view) {
      return VIEW_TO_SECTION[view] || view || "dashboard";
    }
    function viewForSection(sectionId) {
      if (sectionId === "jobsPage") return "jobs";
      if (sectionId === "workersPage") return "workers";
      return sectionId;
    }
    function paramsFromSearch(search = location.search) {
      const raw = Object.fromEntries(new URLSearchParams(search));
      const params = {view: raw.view || "dashboard"};
      Object.entries(raw).forEach(([key, value]) => {
        if (value === "" || value === undefined || value === null) return;
        params[key] = value;
      });
      return params;
    }
    function recommendationParams() {
      return {
        k: document.getElementById("k").value,
        max_per_artist: document.getElementById("maxPerArtist").value,
        exclude_same_album: document.getElementById("excludeSameAlbum").checked ? "true" : "false",
      };
    }
    function textSearchParams() {
      return {
        text_query: document.getElementById("textSearchQuery").value.trim(),
        count: document.getElementById("textSearchCount").value,
        min_similarity: document.getElementById("textSearchMinSimilarity").value,
        max_per_artist: document.getElementById("textSearchMaxPerArtist").value,
        exclude_same_album: document.getElementById("textSearchExcludeSameAlbum").checked ? "true" : "false",
      };
    }
    function libraryParams() {
      return {
        query: document.getElementById("query").value,
        embedding_status: document.getElementById("embeddingStatus").value,
      };
    }
    function browseParams() {
      const params = {
        query: document.getElementById("browseQuery").value,
        embedding_status: document.getElementById("browseEmbeddingStatus").value,
      };
      BROWSE_FACET_KEYS.forEach(key => {
        if (browseFilters[key]) params[key] = browseFilters[key];
      });
      return params;
    }
    function buildRouteSearch(params) {
      const search = new URLSearchParams();
      Object.entries(params).forEach(([key, value]) => {
        if (value === "" || value === undefined || value === null) return;
        search.set(key, String(value));
      });
      return search.toString();
    }
    function activateSection(sectionId) {
      document.querySelectorAll(".section").forEach(section => section.classList.toggle("active", section.id === sectionId));
      document.querySelectorAll("nav button").forEach(button => button.classList.toggle(
        "active",
        button.dataset.nav === sectionId
        || (sectionId === "jobsPage" && button.dataset.nav === "jobs")
        || (sectionId === "workersPage" && button.dataset.nav === "workers")
      ));
    }
    function applySettingsFromParams(params) {
      if (params.model) {
        const modelElement = document.getElementById("model");
        if (modelElement) modelElement.value = params.model;
      }
      if (params.k) document.getElementById("k").value = params.k;
      if (params.max_per_artist) document.getElementById("maxPerArtist").value = params.max_per_artist;
      if (params.exclude_same_album !== undefined) {
        document.getElementById("excludeSameAlbum").checked = params.exclude_same_album !== "false";
      }
    }
    async function restoreRecommendations(params) {
      const seed = params.seed ? Number(params.seed) : null;
      if (!Number.isInteger(seed) || seed <= 0) {
        seedId = null;
        seedTrack = null;
        document.getElementById("seedPanel").innerHTML = `
          <div class="track">
            <div class="title">No seed selected</div>
            <div class="meta">Pick a seed from Library or search here.</div>
          </div>`;
        document.getElementById("refreshSimilarBtn").disabled = true;
        document.getElementById("similarList").innerHTML = "";
        return;
      }
      try {
        seedId = seed;
        seedTrack = await json(`/tracks/${seed}`);
        document.getElementById("seedPanel").innerHTML = renderTrack({...seedTrack, has_embedding: true}, "seed");
        document.getElementById("refreshSimilarBtn").disabled = false;
        await loadSimilar(seed, {updateUrl: false});
      } catch (err) {
        seedId = null;
        seedTrack = null;
        document.getElementById("seedPanel").innerHTML = `
          <div class="track">
            <div class="title">Seed #${seed} not found</div>
            <div class="meta">${esc(err.message)}</div>
          </div>`;
        document.getElementById("refreshSimilarBtn").disabled = true;
        document.getElementById("similarList").innerHTML = "";
      }
    }
    async function applyRoute(params) {
      applyingRoute = true;
      try {
        const view = params.view || "dashboard";
        const sectionId = sectionIdForView(view);
        applySettingsFromParams(params);
        activateSection(sectionId);
        if (sectionId === "library") {
          if (params.query !== undefined) document.getElementById("query").value = params.query;
          if (params.embedding_status) document.getElementById("embeddingStatus").value = params.embedding_status;
          await searchTracks({updateUrl: false});
        } else if (sectionId === "browse") {
          browseFilters = {};
          BROWSE_FACET_KEYS.forEach(key => {
            if (params[key]) browseFilters[key] = params[key];
          });
          if (params.query !== undefined) document.getElementById("browseQuery").value = params.query;
          if (params.embedding_status) document.getElementById("browseEmbeddingStatus").value = params.embedding_status;
          await refreshBrowse({updateUrl: false});
        } else if (sectionId === "metrics") {
          if (params.query !== undefined) document.getElementById("metricsQuery").value = params.query;
          if (params.source) document.getElementById("metricsSource").value = params.source;
          await loadMetricsExplorer({updateUrl: false});
        } else if (sectionId === "recommendations") {
          if (params.seedQuery !== undefined) document.getElementById("seedQuery").value = params.seedQuery;
          await restoreRecommendations(params);
        } else if (sectionId === "textSearch") {
          if (params.text_query !== undefined) document.getElementById("textSearchQuery").value = params.text_query;
          if (params.count) document.getElementById("textSearchCount").value = params.count;
          if (params.min_similarity !== undefined) document.getElementById("textSearchMinSimilarity").value = params.min_similarity;
          if (params.max_per_artist) document.getElementById("textSearchMaxPerArtist").value = params.max_per_artist;
          if (params.exclude_same_album !== undefined) {
            document.getElementById("textSearchExcludeSameAlbum").checked = params.exclude_same_album !== "false";
          }
          renderTextSearchRecent();
          if ((params.text_query || "").trim()) await runTextSearch({updateUrl: false});
        } else if (sectionId === "navidromeLikes") {
          if (params.filter) document.getElementById("likedFilter").value = params.filter;
          if (params.autoload === "1") await loadNavidromeLikes({updateUrl: false});
          renderLikedTracks();
          if (params.refresh === "1" && likedReadyTrackIds().length) {
            await refreshLikedRecommendations({updateUrl: false});
          }
        } else if (sectionId === "instantMix") {
          await loadInstantMixSettings();
          const requests = await loadInstantMixRequests({updateUrl: false});
          if (params.request) await loadInstantMixRequestDetail(params.request, {updateUrl: false});
          else if (requests.length) await loadInstantMixRequestDetail(requests[0].id, {updateUrl: true});
          else renderInstantMixEmptyDetail();
        } else if (sectionId === "evaluation") {
          if (params.index !== undefined && seedBasket.length) {
            const index = Math.max(0, Math.min(Number(params.index), seedBasket.length - 1));
            if (Number.isInteger(index)) await selectEvaluationSeed(index, {updateUrl: false});
          }
        } else if (sectionId === "lostFiles") {
          lostFilesPage = params.page ? Math.max(1, Number(params.page) || 1) : 1;
          await loadLostFiles({updateUrl: false});
        } else if (sectionId === "erroredFiles") {
          erroredFilesPage = params.page ? Math.max(1, Number(params.page) || 1) : 1;
          await loadErroredFiles({updateUrl: false});
        } else if (sectionId === "jobsPage") {
          await refreshJobs({history: true});
          if (params.job) await loadJobDetail(params.job, {updateUrl: false});
        } else if (sectionId === "workersPage") {
          await refreshWorkers();
        } else if (sectionId === "settings") {
          await loadAudioFeaturesSettings();
        }
      } finally {
        applyingRoute = false;
      }
    }
    function mergeRouteParams(params, {reset = false} = {}) {
      if (reset) return {view: params.view || "dashboard", ...params, model: params.model || model()};
      return {...paramsFromSearch(), ...params, model: params.model || model()};
    }
    function pushRouteOnly(params, {replace = false, reset = false} = {}) {
      const merged = mergeRouteParams(params, {reset});
      const search = buildRouteSearch(merged);
      const url = search ? `?${search}` : location.pathname;
      if (replace) history.replaceState({route: merged}, "", url);
      else history.pushState({route: merged}, "", url);
    }
    function syncModelRoute() {
      pushRouteOnly({model: model()}, {replace: true});
    }
    function routeTo(params, {replace = false, reset = false} = {}) {
      const merged = mergeRouteParams(params, {reset});
      const search = buildRouteSearch(merged);
      const url = search ? `?${search}` : location.pathname;
      if (replace) history.replaceState({route: merged}, "", url);
      else history.pushState({route: merged}, "", url);
      return applyRoute(merged);
    }
    function replaceRoute(params, options = {}) {
      return routeTo(params, {...options, replace: true});
    }
    function writeRoutePatch(patch, {replace = false} = {}) {
      return routeTo({...paramsFromSearch(), ...patch, model: patch.model || model()}, {replace});
    }
    function showSection(id) {
      const view = viewForSection(id);
      routeTo({view, model: model()}, {reset: true});
    }
    function syncRecommendationRoute() {
      if (paramsFromSearch().view !== "recommendations" || !seedId) return;
      pushRouteOnly({
        view: "recommendations",
        seed: String(seedId),
        model: model(),
        ...recommendationParams()
      }, {replace: true, reset: true});
    }
    async function refreshStats() {
      if (statsInFlight) return;
      statsInFlight = true;
      try {
        const data = await json(`/stats?model=${encodeURIComponent(model())}`, {timeoutMs: 30000});
        renderDashboardCards(data);
        renderModelCards(data);
        const indexStatus = data.index_status || (data.index_exists ? "ready" : "missing");
        const indexCount = data.index_count ?? "?";
        const embeddingCount = data.index_embedding_count ?? data.embeddings;
        document.getElementById("modelState").textContent =
          `Model file: ${data.model_exists ? "ready" : "missing"} · ${data.model_path} · ` +
          `Index: ${indexStatus} (${indexCount}/${embeddingCount}) · ${data.index}`;
        renderHeadPackStatus(data.head_pack);
      } catch (err) {
        document.getElementById("modelState").textContent = `Stats unavailable: ${err.message}`;
      } finally {
        statsInFlight = false;
      }
    }
    function countText(value) {
      const numeric = Number(value || 0);
      return Number.isFinite(numeric) ? numeric.toLocaleString() : text(value);
    }
    function statusPill(status) {
      const value = status || "missing";
      return `<span class="pill status-${text(value)}">${text(value)}</span>`;
    }
    function statCard(title, value, subtitle, detailsHtml = "", actionHtml = "") {
      return `<div class="stat">
        <h3>${text(title)}</h3>
        <div class="stat-count"><strong>${countText(value)}</strong><span>${text(subtitle)}</span></div>
        <div class="stat-lines">${detailsHtml}</div>
        <div class="stat-actions">${actionHtml}</div>
      </div>`;
    }
    function modelStatCard(className, title, value, subtitle, detailsHtml = "", actionHtml = "") {
      return `<div class="stat model-card${className}">
        <h3>${text(title)}</h3>
        <div class="stat-count"><strong>${countText(value)}</strong><span>${text(subtitle)}</span></div>
        <div class="stat-lines">${detailsHtml}</div>
        <div class="stat-actions">${actionHtml}</div>
      </div>`;
    }
    function detailLine(value) {
      return `<div class="meta">${value}</div>`;
    }
    function rebuildIndexButton(modelName) {
      return `<button class="dashboard-index-btn stat-icon-button" data-model="${text(modelName)}" onclick="startIndex('${text(modelName)}')" title="Rebuild index" aria-label="Rebuild index">&#8635;</button>`;
    }
    function jobRunning(kind) {
      return (lastJobs || []).some(job => job.kind === kind && ["queued", "running"].includes(job.status));
    }
    function disabledAttr(kind) {
      return "";
    }
    function indexStatusText(status) {
      const value = status || "missing";
      return `<span class="index-status-${text(value)}">${text(value)}</span>`;
    }
    function renderDashboardCards(data) {
      const audio = data.audio_features || {};
      const headPack = data.head_pack || {};
      const cards = [
        statCard("Tracks", data.tracks, "total tracks", detailLine("Navidrome catalog"), `<button id="navidromeSyncBtn" onclick="startNavidromeSync()"${disabledAttr("navidrome-sync")}>Sync Navidrome</button>`),
        statCard("Audio features", audio.missing_tracks ?? data.audio_features_missing_tracks, "need audio features", detailLine(`${countText(audio.complete_tracks || 0)} ready / ${countText(data.tracks)} total`), `<button id="analyzeAudioFeaturesBtn" onclick="startAnalyzeAudioFeatures()" title="Analyze audio features"${disabledAttr("analyze-audio-features")}>Analyze missing</button>`),
        statCard("Discogs-EffNet heads", headPack.missing_tracks ?? data.head_pack_missing_tracks, "need head outputs", detailLine(`${countText(headPack.complete_tracks || 0)} ready / ${countText(data.tracks)} total`), `<button id="analyzeHeadsBtn" onclick="startAnalyzeHeads()" title="Analyze Discogs-EffNet heads"${disabledAttr("analyze-heads")}>Analyze missing</button>`),
        statCard("Lost files", data.missing_files, "missing on disk", detailLine("Check unavailable paths"), `<button onclick="routeTo({view: 'lostFiles', model: model()}, {reset: true})">Open</button>`),
        statCard("Errored files", data.analysis_error_count || 0, "analysis errors", detailLine("Latest failed track/model pairs"), `<button onclick="routeTo({view: 'erroredFiles', model: model()}, {reset: true})">Open</button>`)
      ];
      document.getElementById("dashboardCards").innerHTML = cards.join("");
    }
    function renderModelCards(data) {
      const selected = model();
      const total = data.tracks || 0;
      const cards = (data.model_stats || []).map(item => {
        const indexStatus = item.index_status || (item.index_exists ? "ready" : "missing");
        const indexCount = item.index_count ?? "?";
        const indexEmbeddingCount = item.index_embedding_count ?? item.embeddings;
        const selectedClass = item.model === selected ? " selected" : "";
        const details = [
          detailLine(`${countText(item.embeddings)} ready / ${countText(total)} total`),
          `<div class="meta stat-index-row">index ${indexStatusText(indexStatus)} ${countText(indexCount)} / ${countText(indexEmbeddingCount)} ${rebuildIndexButton(item.model)}</div>`
        ].join("");
        return modelStatCard(
          selectedClass,
          item.model,
          item.missing_embeddings,
          "need embeddings",
          details,
          `<button class="dashboard-analyze-btn" data-model="${text(item.model)}" onclick="startAnalyze('${text(item.model)}')"${disabledAttr("analyze")}>Analyze missing</button>`
        );
      }).join("");
      document.getElementById("modelCards").innerHTML = cards || `<div class="meta">No models configured.</div>`;
    }
    function renderHeadPackStatus(pack) {
      const modelFiles = pack.model_files || [];
      const ready = modelFiles.filter(file => file.ready).length;
      const required = modelFiles.length;
      const heads = pack.models ? pack.models.length : 0;
      document.getElementById("headPackSummary").textContent =
        `Model files: ${ready}/${required} loaded`;
      document.getElementById("headPackReadiness").textContent =
        `Loaded files ${ready}/${required}; heads ${heads}`;
      const rows = modelFiles.map(file => `
        <tr>
          <td><span class="pill ${file.ready ? "" : "bad-pill"}">${file.ready ? "loaded" : "missing"}</span></td>
          <td>${file.model || ""}</td>
          <td>${file.kind || ""}</td>
          <td class="path" title="${file.path || ""}">${file.filename || ""}</td>
        </tr>`).join("");
      document.getElementById("headPackModelTable").innerHTML = `
        <table class="model-table">
          <thead><tr><th>Status</th><th>Model</th><th>Kind</th><th>File</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>`;
    }
    function setNavidromeStatus(message, isError = false) {
      const target = document.getElementById("navidromeStatus");
      if (!target) return;
      target.textContent = message;
      target.classList.toggle("error", isError);
    }
    function audioFeaturesExtractor() {
      return document.getElementById("audioFeaturesExtractor").value;
    }
    async function loadAudioFeaturesSettings() {
      const statusTarget = document.getElementById("audioFeaturesStatus");
      if (!statusTarget) return;
      try {
        const data = await json(`/stats?model=${encodeURIComponent(model())}`, {timeoutMs: 30000});
        const audio = data.audio_features || {};
        const extractor = audio.extractor || audioFeaturesExtractor();
        statusTarget.textContent =
          `${extractor}: ${audio.complete_tracks || 0} complete, ${audio.missing_tracks || 0} missing`;
      } catch (err) {
        statusTarget.textContent = `Audio feature status unavailable: ${err.message}`;
      }
    }
    async function loadNavidromeSettings() {
      try {
        const data = await json("/settings/navidrome");
        document.getElementById("navidromeUrl").value = data.url || "";
        document.getElementById("navidromeUser").value = data.user || "";
        document.getElementById("navidromePassword").value = "";
        document.getElementById("navidromeAuthMode").value = data.auth_mode || "token";
        document.getElementById("navidromeTimeoutSeconds").value = data.timeout_seconds || 60;
        document.getElementById("navidromeDownloadMode").value = data.download_mode || "download";
        document.getElementById("navidromeTempDir").value = data.temp_dir || "";
        setNavidromeStatus(data.password_set ? "Navidrome settings loaded; password is saved." : "Navidrome settings loaded; password is not set.");
      } catch (err) {
        setNavidromeStatus(`Failed to load Navidrome settings: ${err.message}`, true);
      }
    }
    async function saveNavidromeSettings() {
      const password = document.getElementById("navidromePassword").value;
      const body = {
        url: document.getElementById("navidromeUrl").value,
        user: document.getElementById("navidromeUser").value,
        auth_mode: document.getElementById("navidromeAuthMode").value,
        timeout_seconds: Number(document.getElementById("navidromeTimeoutSeconds").value || 60),
        download_mode: document.getElementById("navidromeDownloadMode").value,
        temp_dir: document.getElementById("navidromeTempDir").value || null
      };
      if (password) body.password = password;
      try {
        const data = await json("/settings/navidrome", {
          method: "PUT",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(body)
        });
        document.getElementById("navidromePassword").value = "";
        setNavidromeStatus(data.password_set ? "Navidrome settings saved." : "Navidrome settings saved; password is not set.");
      } catch (err) {
        setNavidromeStatus(`Save failed: ${err.message}`, true);
      }
    }
    async function pingNavidrome() {
      try {
        const data = await json("/navidrome/ping", {method: "POST"});
        const version = [data.version, data.server_version].filter(Boolean).join(" / ");
        setNavidromeStatus(`Navidrome ping OK${version ? `: ${version}` : ""}.`);
      } catch (err) {
        setNavidromeStatus(`Navidrome ping failed: ${err.message}`, true);
      }
    }
    async function startNavidromeSync() {
      try {
        await json("/jobs/navidrome-sync", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({page_size: 500, mark_stale: true})
        });
        setNavidromeStatus("Navidrome sync queued.");
        await refreshJobs();
      } catch (err) {
        setNavidromeStatus(`Navidrome sync failed: ${err.message}`, true);
      }
    }
    function setInstantMixStatus(message, isError = false) {
      const target = document.getElementById("instantMixStatus");
      if (!target) return;
      target.textContent = message;
      target.classList.toggle("error", isError);
    }
    async function loadInstantMixSettings() {
      try {
        const data = await json("/instant-mix/settings");
        document.getElementById("instantMixModel").value = data.model || "discogs_multi";
        document.getElementById("instantMixCount").value = data.count ?? 50;
        document.getElementById("instantMixMinSimilarity").value = data.min_similarity ?? "";
        document.getElementById("instantMixMaxPerArtist").value = data.max_per_artist ?? 2;
        document.getElementById("instantMixExcludeSameAlbum").checked = data.exclude_same_album !== false;
        document.getElementById("instantMixCountCollaborationArtists").checked = data.count_collaboration_artists !== false;
        setInstantMixStatus("Instant mix settings loaded.");
      } catch (err) {
        setInstantMixStatus(`Failed to load instant mix settings: ${err.message}`, true);
      }
    }
    async function saveInstantMixSettings() {
      const minSimilarityRaw = document.getElementById("instantMixMinSimilarity").value;
      const body = {
        model: document.getElementById("instantMixModel").value,
        count: Number(document.getElementById("instantMixCount").value || 50),
        min_similarity: minSimilarityRaw === "" ? null : Number(minSimilarityRaw),
        max_per_artist: Number(document.getElementById("instantMixMaxPerArtist").value || 2),
        exclude_same_album: document.getElementById("instantMixExcludeSameAlbum").checked,
        count_collaboration_artists: document.getElementById("instantMixCountCollaborationArtists").checked,
      };
      try {
        await json("/instant-mix/settings", {
          method: "PUT",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(body)
        });
        setInstantMixStatus("Instant mix settings saved.");
        await loadInstantMixSettings();
      } catch (err) {
        setInstantMixStatus(`Save failed: ${err.message}`, true);
      }
    }
    function renderInstantMixEmptyDetail() {
      currentInstantMixRequestId = null;
      document.getElementById("instantMixDetail").innerHTML =
        `<div class="meta">Select a request to inspect returned tracks and parameters.</div>`;
    }
    function renderInstantMixRequestCard(item) {
      const statusClass = item.status === "completed" ? "" : "bad-pill";
      const when = item.created_at ? new Date(item.created_at).toLocaleString() : "";
      const seedTitle = item.seed_track ? label(item.seed_track) : item.seed_item_id;
      const seedMeta = item.seed_track
        ? `#${item.seed_track.id} - ${text(item.seed_track.album) || "no album"}`
        : `Navidrome ${item.seed_item_id}`;
      return `<button class="track" style="text-align:left" onclick="openInstantMixRequest('${encodedArg(item.id)}')">
        <div class="row" style="justify-content:space-between">
          <strong>${esc(seedTitle)}</strong>
          <span class="pill ${statusClass}">${esc(item.status)}</span>
        </div>
        <div class="meta">${esc(seedMeta)}</div>
        <div class="meta">returned ${item.result_count}/${item.effective_count} - min similarity ${item.min_similarity ?? "none"}</div>
        <div class="meta">model ${esc(item.model)}</div>
        <div class="meta">${esc(when)}</div>
      </button>`;
    }
    async function loadInstantMixRequests({updateUrl = true} = {}) {
      try {
        const data = await json("/instant-mix/requests?limit=100");
        const requests = data.results || [];
        const html = requests.map(renderInstantMixRequestCard).join("");
        document.getElementById("instantMixRequests").innerHTML = html || `<div class="meta">No instant mix requests yet.</div>`;
        if (updateUrl && paramsFromSearch().view !== "instantMix") {
          pushRouteOnly({view: "instantMix"}, {replace: true, reset: true});
        }
        return requests;
      } catch (err) {
        document.getElementById("instantMixRequests").innerHTML =
          `<div class="error">Failed to load instant mix requests: ${esc(err.message)}</div>`;
        return [];
      }
    }
    async function openInstantMixRequest(requestId) {
      const decodedId = decodeURIComponent(requestId);
      await routeTo({view: "instantMix", request: decodedId}, {reset: true});
    }
    function backToInstantMixList() {
      routeTo({view: "instantMix"}, {reset: true});
    }
    function renderTrackMetaLine(t) {
      const base = [text(t.genre), t.year || "", text(t.album)].filter(Boolean).map(esc).join(" / ");
      const predictedGenres = (t.genre_discogs400 || [])
        .slice(0, 3)
        .map(prediction => formatPredictionLabel(prediction.label))
        .filter(Boolean)
        .join(", ");
      return [base, predictedGenres].filter(Boolean).join(" · ");
    }
    function renderTrackDetailLine(t) {
      const features = t.card_features || {};
      const left = [];
      const bpm = numericFeatureValue(features.bpm);
      if (bpm !== null) left.push(`BPM ${formatBpm(bpm)}`);
      const key = featureTextValue(features.key);
      const scale = featureTextValue(features.scale);
      if (key || scale) left.push([key, scale].filter(Boolean).map(esc).join(" "));
      if (t.approachability_3c) left.push(`ap ${formatScore(t.approachability_3c.score)}`);
      if (t.engagement_3c) left.push(`eng ${formatScore(t.engagement_3c.score)}`);
      const right = [
        t.year || "",
        t.audio_format ? esc(t.audio_format) : "",
        t.bitrate ? `${esc(t.bitrate)} kbps` : "",
        t.duration ? formatDuration(t.duration) : "",
      ].filter(Boolean);
      if (!left.length && !right.length) return "";
      return `<span class="track-card-left">${left.join(" · ")}</span><span class="track-card-right">${right.join(" · ")}</span>`;
    }
    function numericFeatureValue(feature) {
      if (!feature || feature.value === null || feature.value === undefined) return null;
      const value = Number(feature.value);
      return Number.isFinite(value) ? value : null;
    }
    function featureTextValue(feature) {
      if (!feature) return "";
      return feature.text_value || (feature.value === null || feature.value === undefined ? "" : String(feature.value));
    }
    function formatBpm(value) {
      return Number.isInteger(value) ? String(value) : value.toFixed(1);
    }
    function formatPredictionLabel(label) {
      const parts = String(label || "")
        .split(/---|\//)
        .map(part => part.trim())
        .filter(Boolean);
      return esc(parts.length > 1 ? parts.slice(1).join(" / ") : parts[0] || "");
    }
    function setNavidromeLikeDebug(message, detail = null) {
      const suffix = detail === null || detail === undefined ? "" : ` ${typeof detail === "string" ? detail : JSON.stringify(detail)}`;
      navidromeLikeLastDebug = `${new Date().toLocaleTimeString()} ${message}${suffix}`;
      const target = document.getElementById("navidromeLikeDebug");
      if (target) target.textContent = `Navidrome likes debug: ${navidromeLikeLastDebug}`;
      console.info("[discocs navidrome likes]", message, detail ?? "");
    }
    function bootstrapHeartIcon(filled) {
      const path = filled
        ? "M8 1.314C12.438-3.248 23.534 4.735 8 15-7.534 4.736 3.562-3.248 8 1.314z"
        : "m8 2.748-.717-.737C5.6.281 2.514.878 1.4 3.053.918 3.995.78 5.323 1.508 6.692c.681 1.28 1.997 2.67 3.889 4.068.698.516 1.426.999 2.603 1.774 1.177-.775 1.905-1.258 2.603-1.774 1.892-1.398 3.208-2.788 3.889-4.068.728-1.369.59-2.697.108-3.639-1.114-2.175-4.2-2.772-5.883-1.042L8 2.748zM8 15C-7.333 4.868 3.279-3.04 7.824 1.143c.06.055.119.112.176.171a3.12 3.12 0 0 1 .176-.17C12.72-3.042 23.333 4.867 8 15z";
      return `<svg class="bi ${filled ? "bi-heart-fill" : "bi-heart"}" viewBox="0 0 16 16" aria-hidden="true">
        <path d="${path}"></path>
      </svg>`;
    }
    function scheduleNavidromeLikeIdsRefresh() {
      if (navidromeLikeIdsRefreshScheduled) return;
      navidromeLikeIdsRefreshScheduled = true;
      setTimeout(() => {
        navidromeLikeIdsRefreshScheduled = false;
        fetchAndApplyNavidromeLikeIds({silent: true});
      }, 0);
    }
    function navidromeLikeButton(t) {
      if (!t.navidrome_item_id) return "";
      scheduleNavidromeLikeIdsRefresh();
      return `<button
        class="navidrome-like-button"
        data-track-id="${t.id}"
        data-navidrome-like="1"
        onclick="toggleNavidromeLike(${t.id})"
        title="Like in Navidrome"
        aria-label="Like in Navidrome"
      >${bootstrapHeartIcon(false)}</button>`;
    }
    function applyNavidromeLikeIds(data) {
      const likedTrackIds = new Set((data?.track_ids || []).map(Number));
      document.querySelectorAll(".navidrome-like-button").forEach(button => {
        const trackId = Number(button.dataset.trackId);
        const liked = likedTrackIds.has(trackId);
        button.classList.toggle("like-active", liked);
        const label = liked ? "Liked" : "Like";
        button.title = `${label} in Navidrome`;
        button.setAttribute("aria-label", `${label} in Navidrome`);
        button.innerHTML = bootstrapHeartIcon(liked);
        button.disabled = false;
      });
    }
    async function fetchAndApplyNavidromeLikeIds({silent = false} = {}) {
      try {
        setNavidromeLikeDebug("GET /navidrome/starred/ids start");
        const data = await json(`/navidrome/starred/ids?_=${Date.now()}`, {timeoutMs: 15000});
        applyNavidromeLikeIds(data);
        setNavidromeLikeDebug("GET /navidrome/starred/ids ok", {
          user: data.user,
          mapped_count: data.mapped_count,
          track_ids: data.track_ids || [],
        });
        return data;
      } catch (err) {
        setNavidromeLikeDebug("GET /navidrome/starred/ids failed", err.message);
        if (!silent) throw err;
        return null;
      }
    }
    async function toggleNavidromeLike(trackId) {
      const id = Number(trackId);
      const before = await fetchAndApplyNavidromeLikeIds({silent: false});
      const wasLiked = new Set((before?.track_ids || []).map(Number)).has(id);
      const nextLiked = !wasLiked;
      const buttons = document.querySelectorAll(`.navidrome-like-button[data-track-id="${id}"]`);
      buttons.forEach(button => button.disabled = true);
      try {
        setNavidromeLikeDebug(`PUT /tracks/${id}/navidrome-star start`, {starred: nextLiked});
        await json(`/tracks/${id}/navidrome-star`, {
          method: "PUT",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({starred: nextLiked}),
          timeoutMs: 15000
        });
        setNavidromeLikeDebug(`PUT /tracks/${id}/navidrome-star ok`, {starred: nextLiked});
        await fetchAndApplyNavidromeLikeIds({silent: false});
        if (likedCatalog) {
          await loadNavidromeLikes({updateUrl: false});
        }
      } catch (err) {
        await fetchAndApplyNavidromeLikeIds({silent: true});
        setNavidromeLikeDebug(`PUT /tracks/${id}/navidrome-star failed`, err.message);
        const target = document.getElementById("likedStatusError");
        if (target) target.textContent = err.message;
      } finally {
        buttons.forEach(button => button.disabled = false);
      }
    }
    document.addEventListener("click", event => {
      const button = event.target.closest(".navidrome-like-button");
      if (!button || !button.dataset.navidromeLike) return;
      if (button.hasAttribute("onclick")) return;
      event.preventDefault();
      event.stopPropagation();
      toggleNavidromeLike(button.dataset.trackId);
    });
    function renderRecommendationTrack(t, {seedTrackId = null, rank = null, allowRating = true} = {}) {
      const prefix = rank === null || rank === undefined ? "" : `${rank + 1}. `;
      const score = t.similarity === null || t.similarity === undefined
        ? ""
        : `<span class="track-score-inline">| ${formatScore(t.similarity)}</span>`;
      const rating = allowRating && seedTrackId
        ? `<button class="${t.rating === 3 ? "rating-active" : ""}" onclick="rateForSeed(${seedTrackId}, ${t.id}, 3)">good</button>
            <button class="${t.rating === 2 ? "rating-active" : ""}" onclick="rateForSeed(${seedTrackId}, ${t.id}, 2)">okay</button>
            <button class="${t.rating === 0 ? "rating-active" : ""}" onclick="rateForSeed(${seedTrackId}, ${t.id}, 0)">bad</button>`
        : "";
      const details = renderTrackDetailLine(t);
      return `<div class="track ${t.id === activeTrackId ? "active-track" : ""}">
        <div class="track-body">
          ${coverMarkup(t)}
          <div class="track-main">
            <div class="track-head">
              <div class="title track-title-row">
                <span class="track-title-main">${prefix}<span class="track-id">#${t.id}</span> ${esc(label(t))}</span>
                ${score}
              </div>
            </div>
            <div class="meta">${renderTrackMetaLine(t)}</div>
            ${details ? `<div class="meta track-card-line">${details}</div>` : ""}
          </div>
        </div>
        <div class="track-actions">
          <div class="track-action-group">${rating}${navidromeLikeButton(t)}</div>
          <div class="track-action-group track-command-group">
            <button class="icon-button" onclick="openAnalysis(${t.id})" title="Analysis metadata" aria-label="Analysis metadata">
              <span class="icon-tablet" aria-hidden="true"></span>
            </button>
            <button onclick="playTrack(${t.id}, '${encodedArg(label(t))}')">Play</button>
            <button onclick="addSeed(${t.id})">Add seed</button>
            <button onclick="addToBlendExtra(${t.id})">Add to blend</button>
            <button onclick="setSeed(${t.id})">Seed</button>
          </div>
        </div>
      </div>`;
    }
    function renderInstantMixResult(item, index, seedTrackId) {
      if (item.id) return renderRecommendationTrack(item, {seedTrackId, rank: index});
      return `<div class="track">
        <div class="row" style="justify-content:space-between">
          <div class="title">${index + 1}. ${esc(item.artist || "")} - ${esc(item.title || item.item_id)}</div>
          <span class="score">${formatScore(item.similarity)}</span>
        </div>
        <div class="meta">Navidrome ${esc(item.item_id)} - track #${item.track_id} - distance ${formatScore(item.distance)}</div>
        <div class="meta">${esc(item.album || "")}</div>
      </div>`;
    }
    async function loadInstantMixRequestDetail(requestId, {updateUrl = true} = {}) {
      try {
        const item = await json(`/instant-mix/requests/${encodeURIComponent(requestId)}`);
        currentInstantMixRequestId = item.id;
        const params = item.params || {};
        const results = item.results || [];
        const seedCard = item.seed_track
          ? renderRecommendationTrack(item.seed_track, {allowRating: false})
          : `<div class="track">
              <div class="title">${esc(item.seed_item_id)}</div>
              <div class="meta">Seed track is not available in the local catalog.</div>
            </div>`;
        document.getElementById("instantMixDetail").innerHTML = `
          <div>
            <h2>Seed track</h2>
            ${seedCard}
          </div>
          <div class="track">
            <div class="row" style="justify-content:space-between">
              <strong>${esc(item.seed_item_id)}</strong>
              <span class="pill ${item.status === "completed" ? "" : "bad-pill"}">${esc(item.status)}</span>
            </div>
            <div class="meta">request ${esc(item.id)}</div>
            <div class="meta">track #${item.seed_track_id || ""} - model ${esc(item.model)} - ${esc(item.created_at || "")}</div>
            <div class="meta">requested ${item.requested_count ?? "none"} - effective ${item.effective_count} - returned ${item.result_count}</div>
            <div class="meta">min similarity ${item.min_similarity ?? "none"} - max per artist ${item.max_per_artist} - exclude same album ${item.exclude_same_album ? "yes" : "no"} - count collaboration artists ${params.count_collaboration_artists !== false ? "yes" : "no"}</div>
            ${item.error ? `<div class="error">${esc(item.error)}</div>` : ""}
            <pre class="meta">${esc(JSON.stringify(params, null, 2))}</pre>
          </div>
          <h2>Returned tracks</h2>
          ${results.map((result, index) => renderInstantMixResult(result, index, item.seed_track_id)).join("") || `<div class="meta">No returned tracks.</div>`}`;
        if (updateUrl) {
          pushRouteOnly({view: "instantMix", request: item.id}, {replace: true, reset: true});
        }
      } catch (err) {
        document.getElementById("instantMixDetail").innerHTML =
          `<div class="error">Failed to load instant mix request: ${esc(err.message)}</div>`;
      }
    }
    function coverMarkup(t) {
      return `<div class="cover">
        <img src="/tracks/${t.id}/cover?size=128" loading="lazy" alt="" onerror="this.parentElement.classList.add('empty'); this.remove()">
      </div>`;
    }
    function renderTrack(t, mode) {
      const addSeedButton = ["browse", "library", "seed"].includes(mode)
        ? `<button onclick="addSeed(${t.id})">Add seed</button>` : "";
      const addBlendButton = ["browse", "library", "seed", "blend"].includes(mode)
        ? `<button onclick="addToBlendExtra(${t.id})">Add to blend</button>` : "";
      const details = renderTrackDetailLine(t);
      return `<div class="track ${t.id === seedId ? "selected" : ""} ${t.id === activeTrackId ? "active-track" : ""}">
        <div class="track-body">
          ${coverMarkup(t)}
          <div class="track-main">
            <div class="track-head">
              <div class="title track-title-row">
                <span class="track-title-main"><span class="track-id">#${t.id}</span> ${esc(label(t))}</span>
              </div>
            </div>
            <div class="meta">${renderTrackMetaLine(t)}</div>
            ${details ? `<div class="meta track-card-line">${details}</div>` : ""}
          </div>
        </div>
        <div class="track-actions">
          <div class="track-action-group">${navidromeLikeButton(t)}</div>
          <div class="track-action-group track-command-group">
            <button class="icon-button" onclick="openAnalysis(${t.id})" title="Analysis metadata" aria-label="Analysis metadata">
              <span class="icon-tablet" aria-hidden="true"></span>
            </button>
            <button onclick="playTrack(${t.id}, '${encodedArg(label(t))}')">Play</button>
            ${addSeedButton}
            ${addBlendButton}
            <button onclick="setSeed(${t.id})">Seed</button>
          </div>
        </div>
      </div>`;
    }
    function formatScore(value) {
      if (value === null || value === undefined) return "";
      const number = Number(value);
      return Number.isFinite(number) ? number.toFixed(4) : String(value);
    }
    function formatFeatureValue(feature) {
      const raw = feature.text_value || formatScore(feature.value);
      return [raw, feature.unit || ""].filter(Boolean).join(" ");
    }
    function renderAnalysisOutput(output) {
      const predictions = output.top_predictions || [];
      const tags = predictions.length
        ? `<div class="tag-list">${predictions.map(prediction => `
            <span class="pill" title="${esc(formatScore(prediction.score))}">
              ${prediction.rank}. ${esc(prediction.label)} <span class="score">${esc(formatScore(prediction.score))}</span>
            </span>`).join("")}</div>`
        : `<div class="meta">No top labels for this model.</div>`;
      const scores = output.scores || [];
      const scorePreview = scores.length
        ? `<details>
            <summary class="meta">Scores vector (${scores.length})</summary>
            <pre class="score-list">${esc(scores.map(score => formatScore(score)).join(", "))}</pre>
          </details>`
        : "";
      return `<div class="analysis-output">
        <div class="row" style="justify-content:space-between">
          <strong>${esc(output.model_name)}</strong>
          <span class="pill">${esc(output.dim)} ${esc(output.dtype || "")} ${esc(output.aggregation || "")}</span>
        </div>
        ${tags}
        ${scorePreview}
      </div>`;
    }
    function renderAnalysisFeatures(features) {
      if (!features.length) return `<div class="meta">No audio features stored for this track.</div>`;
      const rows = features.map(feature => `<tr>
        <td>${esc(feature.extractor)}</td>
        <td>${esc(feature.name)}</td>
        <td>${esc(formatFeatureValue(feature))}</td>
        <td>${esc(formatScore(feature.confidence))}</td>
      </tr>`).join("");
      return `<table class="model-table">
        <thead><tr><th>Extractor</th><th>Feature</th><th>Value</th><th>Confidence</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
    }
    async function openAnalysis(trackId) {
      const modal = document.getElementById("analysisModal");
      modal.classList.add("open");
      document.getElementById("analysisSubtitle").textContent = `#${trackId}`;
      document.getElementById("analysisContent").innerHTML = `<div class="meta">Loading analysis metadata...</div>`;
      try {
        const data = await json(`/tracks/${trackId}/analysis`);
        const track = data.track;
        document.getElementById("analysisSubtitle").textContent = `#${track.id} ${label(track)}`;
        const outputs = data.outputs || [];
        document.getElementById("analysisContent").innerHTML = `
          <div class="meta path" title="${esc(track.path)}">${esc(track.path)}</div>
          <div class="analysis-output">
            <strong>Stored analysis</strong>
            <div class="meta">${outputs.length} model outputs; ${(data.features || []).length} audio features.</div>
          </div>
          <div class="analysis-output">
            <strong>Audio features</strong>
            ${renderAnalysisFeatures(data.features || [])}
          </div>
          ${outputs.map(renderAnalysisOutput).join("") || `<div class="meta">No model outputs stored for this track.</div>`}
        `;
      } catch (error) {
        document.getElementById("analysisContent").innerHTML = `<div class="error">${esc(error.message)}</div>`;
      }
    }
    function closeAnalysisModal(event) {
      if (event && event.target !== document.getElementById("analysisModal")) return;
      document.getElementById("analysisModal").classList.remove("open");
    }
    function formatBytes(size) {
      if (!size && size !== 0) return "";
      const units = ["B", "KB", "MB", "GB"];
      let value = Number(size);
      let unit = 0;
      while (value >= 1024 && unit < units.length - 1) {
        value /= 1024;
        unit += 1;
      }
      return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
    }
    function formatDate(value) {
      if (!value) return "";
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
    }
    function renderLostFiles(data) {
      document.getElementById("lostFilesCount").textContent = `${data.count} lost`;
      document.getElementById("lostFilesPage").textContent = `page ${data.page} / ${data.pages}`;
      if (!data.results.length) {
        document.getElementById("lostFilesList").innerHTML = `<div class="meta" style="margin-top:12px">No lost files.</div>`;
        return;
      }
      const rows = data.results.map(track => `
        <tr>
          <td><input type="checkbox" class="lost-checkbox" value="${track.id}"></td>
          <td>#${track.id} ${label(track)}<div class="meta">${text(track.album)}</div></td>
          <td class="path" title="${track.path}">${track.path}</td>
          <td>${formatBytes(track.file_size)}</td>
          <td>${formatDate(track.missing_at)}</td>
        </tr>`).join("");
      document.getElementById("lostFilesList").innerHTML = `
        <table class="model-table">
          <thead><tr><th><input type="checkbox" onchange="toggleLostFilesSelection(this.checked)"></th><th>Track</th><th>Path</th><th>Size</th><th>Disappeared on</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>`;
    }
    async function loadLostFiles() {
      const data = await json(`/lost-files?page=${lostFilesPage}&page_size=${lostFilesPageSize}`);
      if (data.results.length === 0 && data.count > 0 && lostFilesPage > 1) {
        lostFilesPage -= 1;
        return loadLostFiles();
      }
      renderLostFiles(data);
    }
    function renderErroredFiles(data) {
      document.getElementById("erroredFilesCount").textContent = `${data.count} errors`;
      document.getElementById("erroredFilesPage").textContent = `page ${data.page} / ${data.pages}`;
      if (!data.results.length) {
        document.getElementById("erroredFilesList").innerHTML = `<div class="meta" style="margin-top:12px">No errored files.</div>`;
        return;
      }
      const rows = data.results.map(item => {
        const track = `${item.artist || ""}${item.artist && item.title ? " - " : ""}${item.title || ""}` || `track #${item.track_id}`;
        return `
          <tr>
            <td><input type="checkbox" class="errored-checkbox" value="${esc(item.task_id)}"></td>
            <td class="track-cell">#${item.track_id}<div class="meta">${esc(track)}</div></td>
            <td class="path path-cell" title="${esc(item.path)}">${esc(item.path)}</td>
            <td class="model-cell">${esc(item.model_name)}<div class="meta">${esc(item.job_kind)} · ${esc(item.status)} · attempt ${item.attempts}/${item.max_attempts}</div></td>
            <td class="error-cell">
              <pre class="meta error-text">${esc(item.error)}</pre>
              <div class="meta">${esc(item.error_type || "")}${item.stage ? ` · ${esc(item.stage)}` : ""}</div>
            </td>
            <td class="updated-cell">${formatDate(item.updated_at)}</td>
          </tr>`;
      }).join("");
      document.getElementById("erroredFilesList").innerHTML = `
        <table class="model-table error-table">
          <colgroup>
            <col class="check-col">
            <col class="track-col">
            <col class="path-col">
            <col class="model-col">
            <col class="error-col">
            <col class="updated-col">
          </colgroup>
          <thead><tr><th><input type="checkbox" onchange="toggleErroredFilesSelection(this.checked)"></th><th>Track</th><th>Path</th><th>Model</th><th>Error</th><th>Updated</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>`;
    }
    async function loadErroredFiles() {
      const data = await json(`/analysis/errors?page=${erroredFilesPage}&page_size=${erroredFilesPageSize}`);
      if (data.results.length === 0 && data.count > 0 && erroredFilesPage > 1) {
        erroredFilesPage -= 1;
        return loadErroredFiles();
      }
      renderErroredFiles(data);
    }
    function toggleErroredFilesSelection(checked) {
      document.querySelectorAll(".errored-checkbox").forEach(input => { input.checked = checked; });
    }
    async function deleteSelectedErroredFiles() {
      const ids = Array.from(document.querySelectorAll(".errored-checkbox:checked")).map(input => input.value);
      if (!ids.length) return;
      if (!confirm(`Remove ${ids.length} error record(s) from the error list?`)) return;
      await json("/analysis/errors", {
        method: "DELETE",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({task_ids: ids})
      });
      await loadErroredFiles();
    }
    async function deleteAllErroredFiles() {
      if (!confirm("Remove all current error records from the error list?")) return;
      await json("/analysis/errors", {
        method: "DELETE",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({all_errors: true})
      });
      erroredFilesPage = 1;
      await loadErroredFiles();
    }
    async function previousErroredFilesPage() {
      if (erroredFilesPage <= 1) return;
      routeTo({view: "erroredFiles", page: String(erroredFilesPage - 1), model: model()}, {reset: true});
    }
    async function nextErroredFilesPage() {
      const data = await json(`/analysis/errors?page=${erroredFilesPage}&page_size=${erroredFilesPageSize}`);
      if (erroredFilesPage >= data.pages) return;
      routeTo({view: "erroredFiles", page: String(erroredFilesPage + 1), model: model()}, {reset: true});
    }
    function toggleLostFilesSelection(checked) {
      document.querySelectorAll(".lost-checkbox").forEach(input => { input.checked = checked; });
    }
    async function previousLostFilesPage() {
      if (lostFilesPage <= 1) return;
      routeTo({view: "lostFiles", page: String(lostFilesPage - 1), model: model()}, {reset: true});
    }
    async function nextLostFilesPage() {
      const data = await json(`/lost-files?page=${lostFilesPage}&page_size=${lostFilesPageSize}`);
      if (lostFilesPage >= data.pages) return;
      routeTo({view: "lostFiles", page: String(lostFilesPage + 1), model: model()}, {reset: true});
    }
    async function checkMissingFiles() {
      await json("/jobs/check-missing-files", {method: "POST"});
      await refreshJobs();
    }
    async function deleteSelectedLostFiles() {
      const ids = Array.from(document.querySelectorAll(".lost-checkbox:checked")).map(input => Number(input.value));
      if (!ids.length) return;
      if (!confirm(`Remove ${ids.length} lost file record(s) from the library?`)) return;
      await json("/lost-files", {
        method: "DELETE",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({track_ids: ids})
      });
      await loadLostFiles();
      await refreshStats();
    }
    async function deleteAllLostFiles() {
      if (!confirm("Remove all lost file records from the library?")) return;
      await json("/lost-files", {
        method: "DELETE",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({all_missing: true})
      });
      lostFilesPage = 1;
      await loadLostFiles();
      await refreshStats();
    }
    async function searchTracks({updateUrl = true} = {}) {
      const q = document.getElementById("query").value;
      const status = document.getElementById("embeddingStatus").value;
      if (updateUrl && !applyingRoute) {
        routeTo({view: "library", query: q, embedding_status: status, model: model()}, {reset: true});
        return;
      }
      const data = await json(`/tracks?query=${encodeURIComponent(q)}&limit=80&embedding_status=${status}&model=${encodeURIComponent(model())}`);
      document.getElementById("tracksList").innerHTML = data.results.map(t => renderTrack(t, "library")).join("");
    }
    async function searchSeeds() {
      const q = document.getElementById("seedQuery").value;
      const data = await json(`/tracks?query=${encodeURIComponent(q)}&limit=30&embedding_status=ready&model=${encodeURIComponent(model())}`);
      document.getElementById("seedResults").innerHTML = data.results.map(t => renderTrack(t, "seed")).join("");
    }
    function facetButton(kind, item) {
      const active = String(browseFilters[kind] || "") === String(item.value);
      const encodedValue = encodeURIComponent(item.value).replace(/'/g, "%27");
      const display = kind === "folder" ? compactFolder(item.value) : item.value;
      return `<button class="facet-button ${active ? "active" : ""}" title="${item.value}" onclick="setBrowseFilter('${kind}', '${encodedValue}')">
        <span class="facet-name">${display}</span><span class="pill">${item.count}</span>
      </button>`;
    }
    function renderFacets(data) {
      document.getElementById("folderFacets").innerHTML = data.folders.map(item => facetButton("folder", item)).join("") || `<div class="meta">No folders</div>`;
      document.getElementById("genreFacets").innerHTML = data.genres.map(item => facetButton("genre", item)).join("") || `<div class="meta">No tagged genres</div>`;
      document.getElementById("yearFacets").innerHTML = data.years.map(item => facetButton("year", item)).join("") || `<div class="meta">No years</div>`;
      document.getElementById("artistFacets").innerHTML = data.artists.map(item => facetButton("artist", item)).join("") || `<div class="meta">No artists</div>`;
      document.getElementById("albumFacets").innerHTML = data.albums.map(item => facetButton("album", item)).join("") || `<div class="meta">No albums</div>`;
    }
    async function refreshBrowse({updateUrl = true} = {}) {
      if (updateUrl && !applyingRoute) {
        routeTo({view: "browse", model: model(), ...browseParams()}, {reset: true});
        return;
      }
      const status = document.getElementById("browseEmbeddingStatus").value;
      const data = await json(`/browse/facets?embedding_status=${status}&model=${encodeURIComponent(model())}`);
      renderFacets(data);
      await loadBrowseTracks();
    }
    function setBrowseFilter(kind, encodedValue) {
      const value = decodeURIComponent(encodedValue);
      if (String(browseFilters[kind] || "") === value) {
        delete browseFilters[kind];
      } else {
        browseFilters = {[kind]: value};
      }
      refreshBrowse();
    }
    function clearBrowseFilters() {
      browseFilters = {};
      document.getElementById("browseQuery").value = "";
      refreshBrowse();
    }
    async function loadBrowseTracks() {
      const params = new URLSearchParams({
        query: document.getElementById("browseQuery").value,
        limit: "120",
        embedding_status: document.getElementById("browseEmbeddingStatus").value,
        model: model()
      });
      Object.entries(browseFilters).forEach(([key, value]) => params.set(key, value));
      const data = await json(`/tracks?${params.toString()}`);
      const labels = Object.entries(browseFilters).map(([key, value]) => `${key}: ${value}`);
      document.getElementById("browseFilterLabel").textContent = labels.join(" / ") || "all tracks";
      document.getElementById("browseTracks").innerHTML = data.results.map(t => renderTrack(t, "browse")).join("") || `<div class="meta">No tracks match this browser filter.</div>`;
    }
    async function addSeed(id) {
      if (seedBasket.some(track => track.id === id)) {
        showSection("evaluation");
        return;
      }
      const track = await json(`/tracks/${id}`);
      seedBasket.push(track);
      renderSeedBasket();
      showSection("evaluation");
    }
    function clearSeedBasket() {
      seedBasket = [];
      evaluationIndex = -1;
      renderSeedBasket();
      document.getElementById("evaluationSimilarList").innerHTML = "";
      document.getElementById("evaluationSeedPanel").innerHTML = "";
      document.getElementById("evaluationRefreshBtn").disabled = true;
    }
    function renderSeedBasket() {
      document.getElementById("evaluationProgress").textContent = seedBasket.length
        ? `Seed ${Math.max(evaluationIndex + 1, 0)} of ${seedBasket.length}`
        : "No seeds selected";
      document.getElementById("seedBasket").innerHTML = seedBasket.map((track, index) => `
        <div class="track ${index === evaluationIndex ? "selected" : ""}">
          <div class="row" style="justify-content:space-between">
            <div class="title">#${track.id} ${label(track)}</div>
            <button onclick="selectEvaluationSeed(${index})">Open</button>
          </div>
          <div class="meta">${[text(track.genre), track.year || "", text(track.album)].filter(Boolean).join(" / ")}</div>
        </div>`).join("") || `<div class="meta">Add seed tracks from Browse or Library.</div>`;
    }
    async function startEvaluationSession() {
      if (!seedBasket.length) return;
      await selectEvaluationSeed(0);
      showSection("evaluation");
    }
    async function selectEvaluationSeed(index, {updateUrl = true} = {}) {
      if (index < 0 || index >= seedBasket.length) return;
      evaluationIndex = index;
      seedId = seedBasket[index].id;
      seedTrack = seedBasket[index];
      document.getElementById("evaluationSeedPanel").innerHTML = renderTrack({...seedTrack, has_embedding: true}, "evaluation");
      document.getElementById("evaluationRefreshBtn").disabled = false;
      renderSeedBasket();
      await loadSimilar(seedId);
      if (updateUrl && !applyingRoute) {
        routeTo({view: "evaluation", index: String(index), model: model(), ...recommendationParams()}, {reset: true});
      }
    }
    function onLikedFilterChange() {
      if (!applyingRoute) {
        const patch = {view: "navidromeLikes", filter: document.getElementById("likedFilter").value, model: model()};
        if (likedCatalog) patch.autoload = "1";
        writeRoutePatch(patch, {replace: true});
      }
      renderLikedTracks();
    }
    async function nextEvaluationSeed() {
      if (!seedBasket.length) return;
      const next = Math.min(evaluationIndex + 1, seedBasket.length - 1);
      await selectEvaluationSeed(next);
    }
    async function skipEvaluationSeed() {
      await nextEvaluationSeed();
    }
    function readExtraBlendIds() {
      try {
        const parsed = JSON.parse(localStorage.getItem(BLEND_EXTRA_KEY) || "[]");
        return Array.isArray(parsed) ? parsed.map(Number).filter(id => Number.isInteger(id) && id > 0) : [];
      } catch (err) {
        return [];
      }
    }
    function saveExtraBlendIds() {
      localStorage.setItem(BLEND_EXTRA_KEY, JSON.stringify(extraBlendIds));
    }
    function loadExtraBlendIds() {
      extraBlendIds = readExtraBlendIds();
    }
    function likedStatusLabel(status) {
      if (status === "ready") return "ready";
      if (status === "missing_embedding") return "needs embedding";
      return "not synced";
    }
    function renderLikedStatus() {
      const line = document.getElementById("likedStatusLine");
      const detail = document.getElementById("likedStatusDetail");
      if (!likedCatalog) {
        line.textContent = "Source: Navidrome starred · not loaded yet";
        detail.textContent = "Load starred tracks for the configured Navidrome user, then refresh recommendations from their average embedding.";
        document.getElementById("refreshLikedBtn").disabled = true;
        return;
      }
      line.textContent = [
        `Source: Navidrome starred`,
        `User: ${text(likedCatalog.user)}`,
        `Loaded: ${likedCatalog.count}`,
        `Mapped: ${likedCatalog.mapped_count}`,
        `Ready: ${likedCatalog.ready_count}`,
        `Missing embedding: ${likedCatalog.missing_embedding_count}`,
        `Not synced: ${likedCatalog.not_synced_count}`
      ].join(" · ");
      detail.textContent = likedCatalog.ready_count > 0
        ? `Average blend can use ${likedCatalog.ready_count} liked tracks${extraBlendIds.length ? ` plus ${extraBlendIds.length} extra track(s)` : ""}.`
        : "No liked tracks with embeddings yet. Sync catalog and analyze missing tracks.";
      document.getElementById("refreshLikedBtn").disabled = likedReadyTrackIds().length < 1;
    }
    function likedReadyTrackIds() {
      if (!likedCatalog) return [...extraBlendIds];
      const readyIds = (likedCatalog.results || [])
        .filter(item => item.status === "ready" && item.track && item.track.id)
        .map(item => Number(item.track.id));
      const merged = [...readyIds];
      extraBlendIds.forEach(id => {
        if (!merged.includes(id)) merged.push(id);
      });
      return merged;
    }
    function renderLikedTracks() {
      const list = document.getElementById("likedTracksList");
      if (!likedCatalog || !likedCatalog.results.length) {
        list.innerHTML = `<div class="meta">Load Navidrome likes to see starred tracks here.</div>`;
        return;
      }
      const filter = document.getElementById("likedFilter").value;
      const query = document.getElementById("likedLocalQuery").value.trim().toLowerCase();
      const items = likedCatalog.results.filter(item => {
        if (filter !== "all" && item.status !== filter) return false;
        if (!query) return true;
        const track = item.track || {};
        const haystack = `${track.artist || ""} ${track.title || ""} ${track.album || ""} ${item.item_id || ""}`.toLowerCase();
        return haystack.includes(query);
      });
      if (!items.length) {
        list.innerHTML = `<div class="meta">No liked tracks match this filter.</div>`;
        return;
      }
      list.innerHTML = items.map(item => {
        const track = item.track || {};
        const title = track.id ? `#${track.id} ${label(track)}` : `${label(track)} (${item.item_id})`;
        const playButton = track.id
          ? `<button onclick="playTrack(${track.id}, '${encodedArg(label(track))}')">Play</button>`
          : "";
        const likeButton = track.id ? navidromeLikeButton({...track, navidrome_item_id: item.item_id}) : "";
        return `<div class="track">
          <div class="row" style="justify-content:space-between">
            <div class="title">${title}</div>
            <div class="row">
              <span class="pill">${likedStatusLabel(item.status)}</span>
              ${likeButton}
              ${playButton}
            </div>
          </div>
          <div class="meta">${text(track.album)} · ${text(track.path || `navidrome://${item.item_id}`)}</div>
        </div>`;
      }).join("");
    }
    function renderLikedExtraSummary() {
      const target = document.getElementById("likedExtraSummary");
      target.textContent = extraBlendIds.length
        ? `Extra blend tracks: ${extraBlendIds.join(", ")}`
        : "No extra tracks added.";
    }
    function renderLikedSimilarResults(results, metaHtml = "") {
      const html = (results || []).map(t => `
        <div class="track ${t.id === activeTrackId ? "active-track" : ""}">
          <div class="track-body">
            ${coverMarkup(t)}
            <div class="track-main">
              <div class="row" style="justify-content:space-between">
                <div class="title"><span class="score">${t.similarity.toFixed(3)}</span> #${t.id} ${label(t)}</div>
                <div class="row">
                  ${navidromeLikeButton(t)}
                  <button onclick="playTrack(${t.id}, '${encodedArg(label(t))}')">Play</button>
                  <button onclick="setSeed(${t.id})">Seed</button>
                </div>
              </div>
              <div class="meta">${[text(t.genre), t.year || "", text(t.album)].filter(Boolean).join(" / ")}</div>
              <div class="path" title="${t.path}">${t.path}</div>
            </div>
          </div>
        </div>`).join("");
      document.getElementById("likedSimilarList").innerHTML = `${metaHtml}${html || `<div class="meta">No similar tracks.</div>`}`;
    }
    async function loadNavidromeLikes({updateUrl = true} = {}) {
      const errorTarget = document.getElementById("likedStatusError");
      errorTarget.textContent = "";
      try {
        likedCatalog = await json(`/navidrome/starred?model=${encodeURIComponent(model())}`);
        renderLikedStatus();
        renderLikedTracks();
        await fetchAndApplyNavidromeLikeIds({silent: true});
        if (updateUrl && !applyingRoute) {
          writeRoutePatch({
            view: "navidromeLikes",
            autoload: "1",
            filter: document.getElementById("likedFilter").value,
            model: model()
          }, {replace: true});
        }
      } catch (err) {
        errorTarget.textContent = err.message;
      }
    }
    async function refreshLikedRecommendations({updateUrl = true} = {}) {
      const errorTarget = document.getElementById("likedStatusError");
      errorTarget.textContent = "";
      const readyIds = likedReadyTrackIds();
      if (!readyIds.length) {
        errorTarget.textContent = "No ready liked tracks with embeddings. Sync catalog and analyze missing tracks first.";
        document.getElementById("likedSimilarList").innerHTML = `<div class="meta">Analyze liked tracks first.</div>`;
        return;
      }
      const k = document.getElementById("k").value;
      const max = document.getElementById("maxPerArtist").value;
      const exclude = document.getElementById("excludeSameAlbum").checked;
      try {
        let data;
        if (extraBlendIds.length) {
          const params = new URLSearchParams({
            seed_ids: readyIds.join(","),
            model: model(),
            k: String(k),
            max_per_artist: String(max),
            exclude_same_album: String(exclude)
          });
          data = await json(`/tracks/similar/mix?${params.toString()}`);
          document.getElementById("likedSimilarSubtitle").textContent =
            `Recommendations from liked blend · average of ${readyIds.length} tracks`;
          const skipped = (data.skipped_seed_ids || []).length
            ? `<div class="meta">Skipped seeds without embeddings: ${data.skipped_seed_ids.join(", ")}</div>`
            : "";
          renderLikedSimilarResults(data.results, skipped);
        } else {
          const params = new URLSearchParams({
            model: model(),
            count: String(k),
            max_per_artist: String(max),
            exclude_same_album: String(exclude)
          });
          data = await json(`/navidrome/starred/similar?${params.toString()}`);
          document.getElementById("likedSimilarSubtitle").textContent =
            `Recommendations from liked blend · average of ${data.ready_count} liked tracks`;
          const skipped = (data.skipped_seed_ids || []).length
            ? `<div class="meta">Skipped liked tracks without embeddings: ${data.skipped_seed_ids.join(", ")}</div>`
            : "";
          renderLikedSimilarResults(data.results, skipped);
        }
        if (updateUrl && !applyingRoute) {
          writeRoutePatch({
            view: "navidromeLikes",
            autoload: "1",
            refresh: "1",
            filter: document.getElementById("likedFilter").value,
            model: model()
          }, {replace: true});
        }
      } catch (err) {
        errorTarget.textContent = err.message;
      }
    }
    async function addToBlendExtra(id) {
      if (!extraBlendIds.includes(id)) {
        extraBlendIds.push(id);
        saveExtraBlendIds();
      }
      renderLikedExtraSummary();
      showSection("navidromeLikes");
      if (likedCatalog && likedReadyTrackIds().length) await refreshLikedRecommendations();
    }
    function clearLikedBlend() {
      likedCatalog = null;
      extraBlendIds = [];
      saveExtraBlendIds();
      document.getElementById("likedStatusError").textContent = "";
      document.getElementById("likedTracksList").innerHTML = `<div class="meta">Load Navidrome likes to see starred tracks here.</div>`;
      document.getElementById("likedSimilarList").innerHTML = "";
      document.getElementById("likedExtraSearchResults").innerHTML = "";
      document.getElementById("likedSimilarSubtitle").textContent = "Recommendations from liked blend";
      renderLikedExtraSummary();
      renderLikedStatus();
    }
    async function searchLikedExtra() {
      const q = document.getElementById("likedExtraQuery").value;
      const showMissing = document.getElementById("likedExtraShowMissing").checked;
      const embeddingStatus = showMissing ? "all" : "ready";
      const target = document.getElementById("likedExtraSearchResults");
      const errorTarget = document.getElementById("likedStatusError");
      target.innerHTML = `<div class="meta">Searching...</div>`;
      try {
        const data = await json(`/tracks?query=${encodeURIComponent(q)}&limit=30&embedding_status=${embeddingStatus}&model=${encodeURIComponent(model())}`);
        if (!data.results.length) {
          target.innerHTML = `<div class="meta">${showMissing ? "No tracks found." : "No ready tracks found. Enable 'Show tracks without embeddings' or analyze missing tracks."}</div>`;
          return;
        }
        target.innerHTML = data.results.map(t => renderTrack(t, "blend")).join("");
      } catch (err) {
        errorTarget.textContent = err.message;
        target.innerHTML = "";
      }
    }
    function metricFilterFromInputs(name) {
      const summary = metricSummaries.find(item => item.name === name);
      const filter = {name};
      if (summary && summary.value_count) {
        const min = document.getElementById(`metricMin_${name}`)?.value;
        const max = document.getElementById(`metricMax_${name}`)?.value;
        if (min !== "") filter.min_value = Number(min);
        if (max !== "") filter.max_value = Number(max);
      }
      const checked = Array.from(document.querySelectorAll(`[data-metric-value="${name}"]:checked`)).map(input => input.value);
      if (checked.length) filter.text_values = checked;
      return filter;
    }
    function metricsSource() {
      return document.getElementById("metricsSource").value;
    }
    function syncMetricsSourceControls() {
      document.getElementById("metricsExtractor").disabled = metricsSource() === "heads";
    }
    function metricRangeBounds(summary) {
      const min = metricsSource() === "heads" ? 0 : Number(summary.min_value ?? 0);
      const max = metricsSource() === "heads" ? Number(summary.max_value ?? 1) : Number(summary.max_value ?? 1);
      const span = Math.max(max - min, 0.0001);
      const step = metricsSource() === "heads" ? 0.01 : Math.max(span / 200, 0.01);
      return {min, max, step};
    }
    function metricInitialRange(summary) {
      const bounds = metricRangeBounds(summary);
      if (metricsSource() === "heads") {
        return {min: Math.min(0.5, bounds.max), max: bounds.max};
      }
      return {min: bounds.min, max: bounds.max};
    }
    function updateMetricRangeTrack(name) {
      const summary = metricSummaries.find(item => item.name === name);
      if (!summary) return;
      const bounds = metricRangeBounds(summary);
      const minInput = document.getElementById(`metricMin_${name}`);
      const maxInput = document.getElementById(`metricMax_${name}`);
      const track = document.getElementById(`metricTrack_${name}`);
      if (!minInput || !maxInput || !track) return;
      const left = ((Number(minInput.value) - bounds.min) / (bounds.max - bounds.min || 1)) * 100;
      const right = ((Number(maxInput.value) - bounds.min) / (bounds.max - bounds.min || 1)) * 100;
      track.style.background = `linear-gradient(to right, var(--line) 0%, var(--line) ${left}%, var(--accent) ${left}%, var(--accent) ${right}%, var(--line) ${right}%, var(--line) 100%)`;
    }
    function syncMetricRange(encodedName, side, rawValue, {runSearch = false} = {}) {
      const name = decodeURIComponent(encodedName);
      const summary = metricSummaries.find(item => item.name === name);
      if (!summary) return;
      const bounds = metricRangeBounds(summary);
      const minInput = document.getElementById(`metricMin_${name}`);
      const maxInput = document.getElementById(`metricMax_${name}`);
      const minRange = document.getElementById(`metricMinRange_${name}`);
      const maxRange = document.getElementById(`metricMaxRange_${name}`);
      let minValue = minInput.value === "" ? bounds.min : Number(minInput.value);
      let maxValue = maxInput.value === "" ? bounds.max : Number(maxInput.value);
      if (side === "min") minValue = Number(rawValue);
      if (side === "max") maxValue = Number(rawValue);
      if (minValue > maxValue) {
        if (side === "min") maxValue = minValue;
        else minValue = maxValue;
      }
      minInput.value = Number(minValue.toFixed(4));
      maxInput.value = Number(maxValue.toFixed(4));
      minRange.value = minInput.value;
      maxRange.value = maxInput.value;
      updateMetricRangeTrack(name);
      if (runSearch) searchMetrics();
    }
    function activeMetricFilters() {
      return Object.keys(metricFilters)
        .filter(name => metricFilters[name])
        .map(metricFilterFromInputs)
        .filter(filter => filter.name);
    }
    function renderMetricSummary(features) {
      const target = document.getElementById("metricsSummary");
      target.innerHTML = features.map(item => {
        const isHeads = metricsSource() === "heads";
        const range = isHeads
          ? `labels load on select`
          : (item.value_count
            ? `${Number(item.min_value).toFixed(2)}-${Number(item.max_value).toFixed(2)}${item.unit ? ` ${text(item.unit)}` : ""}`
            : `${item.text_count} text values`);
        return `<div class="metric-card ${metricFilters[item.name] ? "active" : ""}" onclick="toggleMetricFilter('${encodedArg(item.name)}')">
          <strong>${text(item.name)}</strong>
          <div class="meta">${range}</div>
          <div class="meta">${item.track_count} tracks</div>
        </div>`;
      }).join("") || `<div class="meta">No metrics yet. Run audio feature analysis first.</div>`;
      const sort = document.getElementById("metricsSort");
      const current = sort.value;
      sort.innerHTML = `<option value="">artist/title</option>` + features
        .filter(item => item.value_count)
        .map(item => `<option value="${text(item.name)}">${text(item.name)}</option>`)
        .join("");
      if (current && features.some(item => item.name === current)) sort.value = current;
    }
    async function renderMetricFilters({signal = null} = {}) {
      const target = document.getElementById("metricFilterList");
      const names = Object.keys(metricFilters).filter(name => metricFilters[name]);
      if (!names.length) {
        target.innerHTML = `<div class="meta">Select metrics above to build filters.</div>`;
        return;
      }
      const valueLists = new Map();
      await Promise.all(names.map(async name => {
        const summary = metricSummaries.find(item => item.name === name);
        if (!summary || !summary.text_count) return;
        const values = await json(`/metrics/features/${encodeURIComponent(name)}/values?source=${encodeURIComponent(metricsSource())}&extractor=${encodeURIComponent(document.getElementById("metricsExtractor").value)}`, {signal, timeoutMs: 30000});
        valueLists.set(name, values.values || []);
      }));
      const blocks = [];
      for (const name of names) {
        const summary = metricSummaries.find(item => item.name === name);
        if (!summary) continue;
        let controls = "";
        if (summary.value_count) {
          const bounds = metricRangeBounds(summary);
          const initial = metricInitialRange(summary);
          const initialMin = Number(initial.min.toFixed(4));
          const initialMax = Number(initial.max.toFixed(4));
          controls += `<div class="range-pair">
            <div class="range-values">
              <input id="metricMin_${text(name)}" type="number" step="${bounds.step}" min="${bounds.min}" max="${bounds.max}" value="${initialMin}" onchange="syncMetricRange('${encodedArg(name)}', 'min', this.value, {runSearch: true})">
              <input id="metricMax_${text(name)}" type="number" step="${bounds.step}" min="${bounds.min}" max="${bounds.max}" value="${initialMax}" onchange="syncMetricRange('${encodedArg(name)}', 'max', this.value, {runSearch: true})">
            </div>
            <div class="range-slider">
              <div id="metricTrack_${text(name)}" class="range-track"></div>
              <input id="metricMinRange_${text(name)}" type="range" min="${bounds.min}" max="${bounds.max}" step="${bounds.step}" value="${initialMin}" oninput="syncMetricRange('${encodedArg(name)}', 'min', this.value)" onchange="syncMetricRange('${encodedArg(name)}', 'min', this.value, {runSearch: true})">
              <input id="metricMaxRange_${text(name)}" type="range" min="${bounds.min}" max="${bounds.max}" step="${bounds.step}" value="${initialMax}" oninput="syncMetricRange('${encodedArg(name)}', 'max', this.value)" onchange="syncMetricRange('${encodedArg(name)}', 'max', this.value, {runSearch: true})">
            </div>
          </div>`;
        }
        if (summary.text_count) {
          const values = valueLists.get(name) || [];
          controls += `<div class="metric-value-list">${values.map(item => `
            <label><input type="checkbox" data-metric-value="${text(name)}" value="${text(item.value)}" onchange="searchMetrics()"><span>${text(item.value)} (${item.track_count})</span></label>
          `).join("")}</div>`;
        }
        blocks.push(`<div class="metric-filter">
          <div class="row" style="justify-content:space-between">
            <strong>${text(name)}</strong>
            <button onclick="toggleMetricFilter('${encodedArg(name)}')">Remove</button>
          </div>
          ${controls}
        </div>`);
      }
      target.innerHTML = blocks.join("");
      names.forEach(updateMetricRangeTrack);
    }
    async function loadMetricsExplorer({updateUrl = true} = {}) {
      syncMetricsSourceControls();
      if (metricsLoadController) metricsLoadController.abort();
      metricsLoadController = new AbortController();
      const signal = metricsLoadController.signal;
      const seq = ++metricsLoadSeq;
      const extractor = document.getElementById("metricsExtractor").value;
      document.getElementById("metricsSummary").innerHTML = `<div class="meta">Loading metrics...</div>`;
      document.getElementById("metricFilterList").innerHTML = `<div class="meta">Loading filters...</div>`;
      try {
      const data = await json(`/metrics/features?source=${encodeURIComponent(metricsSource())}&extractor=${encodeURIComponent(extractor)}`, {signal, timeoutMs: 30000});
      if (seq !== metricsLoadSeq || signal.aborted) return;
      metricSummaries = data.features || [];
      const known = new Set(metricSummaries.map(item => item.name));
      Object.keys(metricFilters).forEach(name => {
        if (!known.has(name)) delete metricFilters[name];
      });
      renderMetricSummary(metricSummaries);
      await renderMetricFilters({signal});
      if (seq !== metricsLoadSeq || signal.aborted) return;
      await searchMetrics({updateUrl});
      } catch (err) {
        if (seq !== metricsLoadSeq || signal.aborted) return;
        document.getElementById("metricsSummary").innerHTML = `<div class="meta">${text(err.message)}</div>`;
        document.getElementById("metricFilterList").innerHTML = `<div class="meta">Metrics failed to load. Try another source or refresh.</div>`;
        document.getElementById("metricsResults").innerHTML = "";
      }
    }
    async function toggleMetricFilter(encodedName) {
      const name = decodeURIComponent(encodedName);
      metricFilters[name] = !metricFilters[name];
      if (metricsLoadController) metricsLoadController.abort();
      metricsLoadController = new AbortController();
      const signal = metricsLoadController.signal;
      renderMetricSummary(metricSummaries);
      try {
        await renderMetricFilters({signal});
      } catch (err) {
        if (signal.aborted) return;
        document.getElementById("metricFilterList").innerHTML = `<div class="meta">${text(err.message)}</div>`;
      }
      await searchMetrics();
    }
    function clearMetricFilters() {
      metricFilters = {};
      if (metricsLoadController) metricsLoadController.abort();
      document.getElementById("metricsQuery").value = "";
      document.getElementById("metricsSort").value = "";
      renderMetricSummary(metricSummaries);
      renderMetricFilters();
      searchMetrics();
    }
    function relevantMetricFeatures(features) {
      const active = activeMetricFilters();
      const activeNames = new Set(active.map(filter => filter.name));
      const selectedLabels = new Map(active.map(filter => [filter.name, new Set(filter.text_values || [])]));
      let relevant = (features || []).filter(feature => {
        if (!activeNames.size) return false;
        if (!activeNames.has(feature.name)) return false;
        const labels = selectedLabels.get(feature.name);
        return !labels || !labels.size || labels.has(feature.text_value);
      });
      if (!relevant.length) {
        const sortBy = document.getElementById("metricsSort").value;
        if (sortBy) relevant = (features || []).filter(feature => feature.name === sortBy).slice(0, 3);
      }
      return relevant.slice(0, 8);
    }
    function renderFeatureChips(features) {
      const relevant = relevantMetricFeatures(features);
      if (!relevant.length) return "";
      return `<div class="feature-chips">${relevant.map(feature => {
        const value = feature.value !== null && feature.value !== undefined
          ? `${Number(feature.value).toFixed(2)}${feature.unit ? ` ${text(feature.unit)}` : ""}`
          : text(feature.text_value);
        return `<span class="feature-chip">${text(feature.name)}: ${value}</span>`;
      }).join("")}</div>`;
    }
    async function searchMetrics({updateUrl = true} = {}) {
      if (metricsSearchController) metricsSearchController.abort();
      metricsSearchController = new AbortController();
      const signal = metricsSearchController.signal;
      const seq = ++metricsSearchSeq;
      const query = document.getElementById("metricsQuery").value;
      document.getElementById("metricsResultCount").textContent = "searching";
      document.getElementById("metricsResults").innerHTML = `<div class="meta">Searching...</div>`;
      try {
      const data = await json("/metrics/search", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        signal,
        timeoutMs: 20000,
        body: JSON.stringify({
          source: metricsSource(),
          extractor: document.getElementById("metricsExtractor").value,
          query,
          filters: activeMetricFilters(),
          sort_by: document.getElementById("metricsSort").value || null,
          sort_direction: metricsSource() === "heads" ? "desc" : "asc",
          limit: Number(document.getElementById("metricsLimit").value || 50)
        })
      });
      if (seq !== metricsSearchSeq || signal.aborted) return;
      document.getElementById("metricsResultCount").textContent = `${data.count} tracks`;
      document.getElementById("metricsResults").innerHTML = data.results.map(t => `
        <div class="track ${t.id === activeTrackId ? "active-track" : ""}">
          <div class="track-body">
            ${coverMarkup(t)}
            <div class="track-main">
              <div class="row" style="justify-content:space-between">
                <div class="title">#${t.id} ${label(t)}</div>
                <div class="row">
                  <button onclick="playTrack(${t.id}, '${encodedArg(label(t))}')">Play</button>
                  <button onclick="setSeed(${t.id})">Seed</button>
                </div>
              </div>
              <div class="meta">${[text(t.genre), t.year || "", text(t.album)].filter(Boolean).join(" / ")}</div>
              ${renderFeatureChips(t.features)}
              <div class="path" title="${t.path}">${t.path}</div>
            </div>
          </div>
        </div>`).join("") || `<div class="meta">No tracks match these metric filters.</div>`;
      if (updateUrl && !applyingRoute) {
        pushRouteOnly({view: "metrics", source: metricsSource(), query, model: model()}, {replace: true, reset: true});
      }
      } catch (err) {
        if (seq !== metricsSearchSeq || signal.aborted) return;
        document.getElementById("metricsResultCount").textContent = "search failed";
        document.getElementById("metricsResults").innerHTML = `<div class="meta">${text(err.message)}</div>`;
      }
    }
    async function setSeed(id) {
      routeTo({
        view: "recommendations",
        seed: String(id),
        model: model(),
        ...recommendationParams()
      }, {reset: true});
    }
    async function loadSimilar(id, {updateUrl = false} = {}) {
      if (!id) return;
      const k = document.getElementById("k").value;
      const max = document.getElementById("maxPerArtist").value;
      const exclude = document.getElementById("excludeSameAlbum").checked;
      const data = await json(`/tracks/${id}/similar?model=${encodeURIComponent(model())}&k=${k}&max_per_artist=${max}&exclude_same_album=${exclude}`);
      currentSimilarTracks = data.results.map(t => ({id: t.id, label: label(t)}));
      const html = data.results.map(t => renderRecommendationTrack(t, {seedTrackId: id})).join("");
      document.getElementById("similarList").innerHTML = html;
      document.getElementById("evaluationSimilarList").innerHTML = html;
      if (updateUrl && !applyingRoute && paramsFromSearch().view === "recommendations" && seedId) {
        syncRecommendationRoute();
      }
    }
    function setTextSearchQuery(query) {
      document.getElementById("textSearchQuery").value = decodeURIComponent(query);
      saveSettings();
    }
    function renderTextSearchRecent() {
      const target = document.getElementById("textSearchRecent");
      if (!target) return;
      target.innerHTML = textSearchRecentQueries.map(query => `
        <div class="track">
          <div class="track-body">
            <div class="track-main">
              <div class="title">${esc(query)}</div>
            </div>
          </div>
          <div class="track-actions">
            <button onclick="setTextSearchQuery('${encodedArg(query)}'); runTextSearch()">Run</button>
          </div>
        </div>
      `).join("") || `<div class="meta">No recent text searches yet.</div>`;
    }
    function rememberTextSearchQuery(query) {
      textSearchRecentQueries = [query, ...textSearchRecentQueries.filter(item => item !== query)].slice(0, 10);
      renderTextSearchRecent();
    }
    function clearTextSearch() {
      document.getElementById("textSearchQuery").value = "";
      document.getElementById("textSearchResults").innerHTML = `<div class="meta">Describe the music you want to find.</div>`;
      document.getElementById("textSearchStatus").textContent = "Model: muq_mulan";
      pushRouteOnly({view: "textSearch", model: model()}, {replace: true, reset: true});
      saveSettings();
    }
    async function runTextSearch({updateUrl = true} = {}) {
      const query = document.getElementById("textSearchQuery").value.trim();
      const target = document.getElementById("textSearchResults");
      const status = document.getElementById("textSearchStatus");
      if (!query) {
        target.innerHTML = `<div class="error">Enter a text query.</div>`;
        return;
      }
      const minSimilarityRaw = document.getElementById("textSearchMinSimilarity").value;
      const payload = {
        query,
        count: Number(document.getElementById("textSearchCount").value || 50),
        min_similarity: minSimilarityRaw === "" ? null : Number(minSimilarityRaw),
        max_per_artist: Number(document.getElementById("textSearchMaxPerArtist").value || 2),
        exclude_same_album: document.getElementById("textSearchExcludeSameAlbum").checked,
      };
      status.textContent = "Searching muq_mulan...";
      target.innerHTML = `<div class="meta">Searching...</div>`;
      try {
        const started = performance.now();
        const data = await json("/text-search", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload),
          timeoutMs: 120000,
        });
        const seconds = ((performance.now() - started) / 1000).toFixed(1);
        rememberTextSearchQuery(query);
        currentSimilarTracks = data.results.map(t => ({id: t.id, label: label(t)}));
        target.innerHTML = data.results.map((track, index) => (
          renderRecommendationTrack(track, {rank: index, allowRating: false})
        )).join("") || `<div class="meta">No tracks matched this query.</div>`;
        const simRange = data.similarity_min === null || data.similarity_min === undefined
          ? "similarity n/a"
          : `similarity ${formatScore(data.similarity_min)}-${formatScore(data.similarity_max)} avg ${formatScore(data.similarity_avg)}`;
        status.textContent = `Model: ${data.model} - returned ${data.results.length}/${payload.count} in ${seconds}s - ${simRange} - norm ${formatScore(data.vector_norm)}`;
        if (updateUrl && !applyingRoute) {
          pushRouteOnly({view: "textSearch", model: model(), ...textSearchParams()}, {replace: true, reset: true});
        }
      } catch (err) {
        status.textContent = "Text search failed";
        target.innerHTML = `<div class="error">${esc(err.message)}</div>`;
      }
    }
    async function playTrack(id, encodedLabel) {
      activeTrackId = id;
      document.getElementById("playerError").textContent = "";
      document.getElementById("nowPlaying").textContent = decodeURIComponent(encodedLabel);
      const player = document.getElementById("audioPlayer");
      player.src = `/tracks/${id}/audio`;
      try {
        await player.play();
      } catch (err) {
        document.getElementById("playerError").textContent = "Click play in the audio controls if autoplay is blocked.";
      }
      await searchTracks({updateUrl: false});
      if (seedId) await loadSimilar(seedId);
    }
    async function refreshSimilarTracks() {
      if (seedId) await loadSimilar(seedId, {updateUrl: true});
    }
    async function playNextSimilarTrack() {
      const index = currentSimilarTracks.findIndex(track => track.id === activeTrackId);
      if (index < 0 || index >= currentSimilarTracks.length - 1) return;
      const next = currentSimilarTracks[index + 1];
      await playTrack(next.id, encodedArg(next.label));
    }
    async function rate(resultId, rating) {
      if (!seedId) return;
      await rateForSeed(seedId, resultId, rating);
    }
    async function rateForSeed(seedTrackId, resultId, rating) {
      if (!seedTrackId) return;
      await json("/feedback", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({seed_track_id: seedTrackId, result_track_id: resultId, model: model(), rating})
      });
      if (seedId && Number(seedTrackId) === Number(seedId)) await loadSimilar(seedId);
      if (currentInstantMixRequestId && paramsFromSearch().view === "instantMix") {
        await loadInstantMixRequestDetail(currentInstantMixRequestId, {updateUrl: false});
      }
    }
    async function startAnalyze(modelName = model()) {
      const rawLimit = document.getElementById("limit").value;
      const rawWorkers = document.getElementById("workers").value;
      const rawTfThreads = document.getElementById("tfThreads").value;
      const executionMode = document.getElementById("analyzeExecutionMode").value;
      const parsedLimit = rawLimit ? Number(rawLimit) : null;
      await json("/jobs/analyze", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          model: modelName,
          limit: parsedLimit && parsedLimit > 0 ? parsedLimit : null,
          workers: rawWorkers ? Number(rawWorkers) : 4,
          tf_threads: rawTfThreads ? Number(rawTfThreads) : 4,
          execution_mode: executionMode,
          local_executor_enabled: executionMode !== "remote"
        })
      });
      await refreshJobs();
    }
    async function downloadHeadModels() {
      await json("/jobs/download-head-models", {method: "POST"});
      await refreshJobs();
    }
    async function startAnalyzeHeads() {
      const rawLimit = document.getElementById("limit").value;
      const executionMode = document.getElementById("analyzeExecutionMode").value;
      const parsedLimit = rawLimit ? Number(rawLimit) : null;
      await json("/jobs/analyze-heads", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          limit: parsedLimit && parsedLimit > 0 ? parsedLimit : null,
          execution_mode: executionMode,
          local_executor_enabled: executionMode !== "remote"
        })
      });
      await refreshJobs();
    }
    async function startAnalyzeAudioFeatures() {
      const rawLimit = document.getElementById("limit").value;
      const rawWorkers = document.getElementById("audioFeatureWorkers").value;
      const executionMode = document.getElementById("analyzeExecutionMode").value;
      const parsedLimit = rawLimit ? Number(rawLimit) : null;
      await json("/jobs/analyze-audio-features", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          limit: parsedLimit && parsedLimit > 0 ? parsedLimit : null,
          workers: rawWorkers ? Number(rawWorkers) : 4,
          execution_mode: executionMode,
          local_executor_enabled: executionMode !== "remote"
        })
      });
      await refreshJobs();
    }
    async function rescanAudioFeatures() {
      const rawLimit = document.getElementById("limit").value;
      const rawWorkers = document.getElementById("audioFeatureWorkers").value;
      const executionMode = document.getElementById("analyzeExecutionMode").value;
      const extractor = audioFeaturesExtractor();
      const parsedLimit = rawLimit ? Number(rawLimit) : null;
      const limit = parsedLimit && parsedLimit > 0 ? parsedLimit : null;
      const scope = limit ? `${limit} active track(s)` : "all active tracks";
      const statusTarget = document.getElementById("audioFeaturesRescanStatus");
      if (!confirm(`Delete existing ${extractor} features for ${scope} and queue them for re-analysis?`)) return;
      try {
        const data = await json("/jobs/analyze-audio-features", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            limit,
            workers: rawWorkers ? Number(rawWorkers) : 4,
            execution_mode: executionMode,
            local_executor_enabled: executionMode !== "remote",
            reset_existing: true,
            extractor
          })
        });
        if (statusTarget) {
          statusTarget.textContent =
            `Queued rescan for ${extractor}. Deleted ${data.deleted_features || 0} stored feature row(s).`;
        }
        await refreshStats();
        await loadAudioFeaturesSettings();
        await refreshJobs();
      } catch (err) {
        if (statusTarget) statusTarget.textContent = `Rescan failed: ${err.message}`;
      }
    }
    async function startIndex(modelName = model()) {
      await json("/jobs/index", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({model: modelName})
      });
      await refreshJobs();
    }
    async function cancelJob(jobId) {
      if (!confirm("Cancel this job? Queued and leased tasks will be marked cancelled.")) return;
      await json(`/jobs/${encodeURIComponent(jobId)}/cancel`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({reason: "Cancelled from web UI"})
      });
      await refreshJobs();
    }
    async function loadJobDetail(jobId, {updateUrl = true} = {}) {
      const data = await json(`/jobs/${encodeURIComponent(jobId)}`);
      const tasks = data.tasks || [];
      const job = data.job || {};
      const taskHtml = tasks.length ? tasks.map(task => `
        <div class="job status-${text(task.status)}">
          <div class="row" style="justify-content:space-between">
            <strong>${text(task.status)} · ${text(task.stage || "unknown")}</strong>
            <span class="pill">${text(task.model_name || "")}</span>
          </div>
          <div class="meta">${text(task.path || "")}</div>
          <div class="meta">task ${text(task.task_id || "")}, track ${text(task.track_id || "")}, attempts ${task.attempts || 0}/${task.max_attempts || 0}</div>
          <div class="meta">worker ${text(task.lease_owner || "")}${task.lease_expires_at ? `, lease expires ${text(task.lease_expires_at)}` : ""}</div>
          ${task.error ? `<div class="meta">error: ${text(task.error_type || "Error")} - ${text(task.error)}</div>` : ""}
        </div>`).join("") : `<div class="meta">No queued, leased, retryable, or final-failed tasks for this job.</div>`;
      document.getElementById("jobDetail").innerHTML = `
        <div class="job status-${text(job.status || "")}">
          <div class="row" style="justify-content:space-between">
            <strong>${text(job.kind || "job")}</strong>
            <span class="pill">${text(job.status || "")}</span>
          </div>
          <div class="meta">${text(job.message || "")}</div>
          <div class="meta">done ${job.done || 0}/${job.total || 0}, queued ${job.queued || 0}, leased ${job.leased || 0}, failed ${job.failed || 0}</div>
        </div>
        ${taskHtml}`;
      activateSection("jobsPage");
      if (updateUrl && !applyingRoute) {
        pushRouteOnly({view: "jobs", job: jobId, model: model()}, {reset: true});
      }
    }
    function renderWorkers(workers) {
      const workerList = document.getElementById("workersList");
      const workerSummary = document.getElementById("workersSummary");
      if (!workerList || !workerSummary) return;
      const connectedWorkers = workers.filter(worker => worker.connected);
      const staleWorkers = workers.filter(worker => !worker.connected);
      const available = connectedWorkers.filter(worker => !worker.current_task_id).length;
      const claimed = connectedWorkers.reduce((sum, worker) => sum + Number(worker.claimed_count || 0), 0);
      const completed = connectedWorkers.reduce((sum, worker) => sum + Number(worker.completed_count || 0), 0);
      const failed = connectedWorkers.reduce((sum, worker) => sum + Number(worker.failed_count || 0), 0);
      workerSummary.innerHTML = `
        <div class="stat"><strong>${connectedWorkers.length}</strong><span>connected</span></div>
        <div class="stat"><strong>${available}</strong><span>available</span></div>
        <div class="stat"><strong>${claimed}</strong><span>claimed tasks</span></div>
        <div class="stat"><strong>${completed}</strong><span>completed tasks</span></div>
        <div class="stat"><strong>${failed}</strong><span>failed tasks</span></div>
        <div class="stat"><strong>${staleWorkers.length}</strong><span>stale hidden</span></div>`;
      const workerHtml = connectedWorkers.map(worker => {
        const models = (worker.models || []).length ? (worker.models || []).join(", ") : "no advertised models";
        const stage = worker.stage || "idle";
        const status = worker.display_status || worker.status || "unknown";
        const lastSeen = worker.last_seen_at || "";
        const created = worker.created_at || "";
        const updated = worker.updated_at || "";
        return `<div class="job status-${text(status)}">
          <div class="row" style="justify-content:space-between">
            <strong>${text(worker.worker_id)}</strong><span class="pill">${text(status)}</span>
          </div>
          <div class="meta">models: ${text(models)}</div>
          <div class="meta">stage: ${text(stage)}${worker.message ? ` - ${text(worker.message)}` : ""}</div>
          <div class="meta">claimed ${worker.claimed_count || 0}, completed ${worker.completed_count || 0}, failed ${worker.failed_count || 0}, released ${worker.released_count || 0}</div>
          ${worker.current_task_id ? `<div class="meta">current task: ${text(worker.current_task_id)}</div>` : ""}
          <div class="meta">last seen: ${text(lastSeen)}${updated ? `, updated: ${text(updated)}` : ""}${created ? `, created: ${text(created)}` : ""}</div>
        </div>`;
      }).join("");
      const staleNote = staleWorkers.length
        ? `<div class="meta">${staleWorkers.length} stale worker record(s) hidden; TTL is ${workers[0]?.connected_ttl_seconds || 180}s.</div>`
        : "";
      workerList.innerHTML = workerHtml || `<div class="meta">No connected workers right now.</div>`;
      workerList.innerHTML += staleNote;
    }
    async function refreshWorkers() {
      try {
        const data = await json("/workers", {timeoutMs: 8000});
        renderWorkers(data.workers || []);
      } catch (err) {
        const workerList = document.getElementById("workersList");
        const workerSummary = document.getElementById("workersSummary");
        if (workerSummary) workerSummary.innerHTML = "";
        if (workerList) workerList.innerHTML = `<div class="meta">Workers unavailable: ${text(err.message)}</div>`;
      }
    }
    async function refreshJobs({history = false} = {}) {
      if (jobsInFlight) return;
      jobsInFlight = true;
      try {
      const jobsUrl = history ? "/jobs?include_completed=true&detail=true" : "/jobs";
      const data = await json(jobsUrl, {timeoutMs: 8000});
      lastJobs = data.jobs;
      const setDisabled = (id, disabled) => {
        const element = document.getElementById(id);
        if (element) element.disabled = disabled;
      };
      setDisabled("navidromeSyncBtn", false);
      setDisabled("analyzeHeadsBtn", false);
      setDisabled("downloadHeadsBtn", false);
      setDisabled("analyzeAudioFeaturesBtn", false);
      setDisabled("rescanAudioFeaturesBtn", false);
      setDisabled("checkMissingBtn", false);
      document.querySelectorAll(".dashboard-analyze-btn").forEach(button => {
        button.disabled = false;
      });
      document.querySelectorAll(".dashboard-index-btn").forEach(button => {
        button.disabled = false;
      });
      const html = data.jobs.map(job => {
        const total = job.total || 0;
        const percent = total ? Math.round(((job.done + job.failed) / total) * 100) : (job.status === "completed" ? 100 : 0);
        const waiting = job.status === "deferred";
        const terminal = !["queued", "running", "deferred"].includes(job.status);
        const elapsed = !waiting && job.elapsed_seconds ? `${formatDuration(job.elapsed_seconds)} ${terminal ? "duration" : "elapsed"}` : "";
        const recentWindow = job.recent_rate_window_seconds ? Math.round(job.recent_rate_window_seconds / 60) : 5;
        const currentRate = job.recent_tracks_per_min ? `current ${job.recent_tracks_per_min.toFixed(1)} tracks/min (${recentWindow}m)` : "";
        const avgRate = job.tracks_per_min ? `avg ${job.tracks_per_min.toFixed(1)} tracks/min` : "";
        const rate = [currentRate, avgRate].filter(Boolean).join(" / ");
        const eta = job.eta_seconds ? `${formatDuration(job.eta_seconds)} ETA` : "";
        const timing = [elapsed, rate, eta].filter(Boolean).join(" - ");
        const startedAt = job.created_at || (job.created_at_epoch ? new Date(job.created_at_epoch * 1000).toLocaleString() : "");
        const updatedAt = job.updated_at || "";
        const finishedAt = job.finished_at_iso || "";
        const workerLine = (job.leased_workers || []).length
          ? `workers: ${(job.leased_workers || []).map(item => `${text(item.worker_id)}(${item.count})`).join(", ")}`
          : "";
        const breakdown = (job.status_breakdown || []).map(item => `${text(item.status)}${item.stage ? `/${text(item.stage)}` : ""}: ${item.count}`).join(", ");
        const oldestLease = job.oldest_lease ? `oldest lease: ${text(job.oldest_lease.worker_id || "")}, ${formatDuration(job.oldest_lease_age || 0)}, ${text(job.oldest_lease.stage || "")}` : "";
        const canCancel = ["queued", "running", "deferred"].includes(job.status);
        const queueLabel = waiting && job.queue_position ? `#${job.queue_position} waiting` : job.status;
        return `<div class="job status-${job.status}">
          <div class="row" style="justify-content:space-between">
            <strong>${job.kind}</strong>
            <div class="row">
              <button onclick="loadJobDetail('${job.id}')">Details</button>
              ${canCancel ? `<button onclick="cancelJob('${job.id}')">Cancel</button>` : ""}
              <span class="pill">${queueLabel}</span>
            </div>
          </div>
          <div class="meta">${job.message}</div>
          <div class="meta">started: ${text(startedAt)}${updatedAt ? `, updated: ${text(updatedAt)}` : ""}${finishedAt ? `, finished: ${text(finishedAt)}` : ""}</div>
          ${job.status_hint ? `<div class="meta">${text(job.status_hint)}</div>` : ""}
          ${workerLine ? `<div class="meta">${workerLine}</div>` : ""}
          ${oldestLease ? `<div class="meta">${oldestLease}</div>` : ""}
          ${breakdown ? `<div class="meta">breakdown: ${breakdown}</div>` : ""}
          ${job.last_error ? `<div class="meta">last error: ${text(job.last_error.error_type || "Error")} ${text(job.last_error.stage || "")} - ${text(job.last_error.error || "")}</div>` : ""}
          ${job.error_detail ? `<pre class="meta">${text(job.error_detail)}</pre>` : ""}
          ${job.current ? `<div class="meta">current: ${job.current}</div>` : ""}
          <div class="meta">done ${job.done}${total ? ` / ${total}` : ""}, queued ${job.queued || 0}, leased ${job.leased || 0}, failed ${job.failed || 0}</div>
          ${timing ? `<div class="meta">${timing}</div>` : ""}
          <div class="bar"><div class="fill" style="width:${percent}%"></div></div>
        </div>`;
      }).join("");
      if (history) {
        document.getElementById("jobs").innerHTML = html || `<div class="meta">No jobs yet</div>`;
      } else {
        document.getElementById("dashboardJobs").innerHTML = html || `<div class="meta">No active jobs</div>`;
        if (document.getElementById("jobsPage").classList.contains("active")) {
          document.getElementById("jobs").innerHTML = html || `<div class="meta">No active jobs</div>`;
        }
      }
      } catch (err) {
        const target = history ? document.getElementById("jobs") : document.getElementById("dashboardJobs");
        if (target) target.innerHTML = `<div class="meta">Jobs unavailable: ${text(err.message)}</div>`;
      } finally {
        jobsInFlight = false;
      }
    }
    async function refreshAll() {
      await refreshStats();
      await refreshJobs();
      await loadNavidromeSettings();
      await loadAudioFeaturesSettings();
      await fetchAndApplyNavidromeLikeIds({silent: true});
      await loadInstantMixSettings();
      renderSeedBasket();
      loadExtraBlendIds();
      renderLikedExtraSummary();
      renderLikedStatus();
      renderLikedTracks();
      await applyRoute(paramsFromSearch());
    }
    async function onModelChange() {
      saveSettings();
      syncModelRoute();
      refreshWorkerCommand();
      await refreshAll();
    }
    async function initFromUrl() {
      const params = paramsFromSearch();
      applySettingsFromParams(params);
      if (!location.search || !new URLSearchParams(location.search).get("view")) {
        await replaceRoute({view: "dashboard", model: model()}, {reset: true});
        await refreshStats();
        await refreshJobs();
        await loadNavidromeSettings();
        await loadAudioFeaturesSettings();
        await fetchAndApplyNavidromeLikeIds({silent: true});
        await loadInstantMixSettings();
        loadExtraBlendIds();
        renderLikedExtraSummary();
        return;
      }
      await applyRoute(params);
      await refreshStats();
      await refreshJobs();
      await loadNavidromeSettings();
      await loadAudioFeaturesSettings();
      await fetchAndApplyNavidromeLikeIds({silent: true});
      await loadInstantMixSettings();
      loadExtraBlendIds();
      renderLikedExtraSummary();
      renderLikedStatus();
    }
    document.getElementById("model").addEventListener("change", onModelChange);
    document.getElementById("query").addEventListener("keydown", event => {
      if (event.key === "Enter") searchTracks();
    });
    document.getElementById("seedQuery").addEventListener("keydown", event => {
      if (event.key === "Enter") searchSeeds();
    });
    document.getElementById("likedExtraQuery").addEventListener("keydown", event => {
      if (event.key === "Enter") searchLikedExtra();
    });
    document.getElementById("likedLocalQuery").addEventListener("keydown", event => {
      if (event.key === "Enter") renderLikedTracks();
    });
    document.getElementById("browseQuery").addEventListener("keydown", event => {
      if (event.key === "Enter") refreshBrowse();
    });
    document.getElementById("metricsQuery").addEventListener("keydown", event => {
      if (event.key === "Enter") searchMetrics();
    });
    document.getElementById("textSearchQuery").addEventListener("keydown", event => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) runTextSearch();
    });
    document.getElementById("k").addEventListener("change", syncRecommendationRoute);
    document.getElementById("maxPerArtist").addEventListener("change", syncRecommendationRoute);
    document.getElementById("excludeSameAlbum").addEventListener("change", syncRecommendationRoute);
    window.addEventListener("popstate", () => {
      applyRoute(paramsFromSearch());
    });
    document.getElementById("audioPlayer").addEventListener("error", () => {
      document.getElementById("playerError").textContent = "file not mounted";
    });
    document.getElementById("audioPlayer").addEventListener("ended", playNextSimilarTrack);
    loadSettings();
    bindSettingsAutosave();
    renderTextSearchRecent();
    refreshWorkerCommand();
    initFromUrl();
    setInterval(() => {
      if (document.hidden) return;
      refreshStats();
      refreshJobs();
    }, 5000);
  </script>
</body>
</html>
"""
