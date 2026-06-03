from __future__ import annotations

import base64
import binascii
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
import logging
import multiprocessing
import os
from pathlib import Path
import socket
import sqlite3
from threading import Lock
import time
from time import perf_counter
from datetime import datetime
import traceback
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
import numpy as np

from app.audio_features import AUDIO_FEATURE_EXTRACTOR, AudioFeatureAnalyzer
from app.config import MODEL_FILES, Settings
from app.embedder import DiscogsEffnetEmbedder
from app.head_pack import (
    DISCOGS_EFFNET_HEADS,
    DiscogsEffnetHeadPackAnalyzer,
    HeadOutput,
    download_model_file,
    download_head_pack_models,
    head_pack_readiness,
    required_model_files,
)
from app.logging_config import configure_logging, get_analysis_logger
from app.recommender import Recommender, build_index
from app.scanner import scan_music_folder
from app.store import (
    AnalysisTask,
    Store,
    Track,
    TrackFeature,
    TrackPrediction,
    similar_track_dict,
    track_dict,
    track_listing_dict,
)


configure_logging()
logger = logging.getLogger(__name__)
analysis_logger = get_analysis_logger()
app = FastAPI(title="discocs", version="0.1.0")
JOBS_LOCK = Lock()
JOBS: dict[str, "JobStatus"] = {}
ANALYZE_EXECUTORS_LOCK = Lock()
ANALYZE_EXECUTORS: set[ProcessPoolExecutor] = set()
SHUTDOWN_REQUESTED = False
MAX_ANALYZE_WORKERS = max(1, os.cpu_count() or 1)
DEFAULT_ANALYZE_WORKERS = min(4, MAX_ANALYZE_WORKERS)
MAX_ANALYZE_TF_THREADS = MAX_ANALYZE_WORKERS
DEFAULT_ANALYZE_TF_THREADS = min(4, MAX_ANALYZE_TF_THREADS)


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
    finished_at: float | None = None


class ScanRequest(BaseModel):
    music_dir: str


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
    local_executor_enabled: bool = True
    max_attempts: int = Field(default=3, ge=1, le=20)
    execution_mode: str = Field(default="both", pattern="^(both|local|remote)$")


class DeleteTracksRequest(BaseModel):
    track_ids: list[int] = Field(default_factory=list)
    all_missing: bool = False


class DeleteAnalysisErrorsRequest(BaseModel):
    task_ids: list[str] = Field(default_factory=list)
    all_errors: bool = False


class IndexRequest(BaseModel):
    model: str = "discogs_multi"


class FeedbackRequest(BaseModel):
    seed_track_id: int
    result_track_id: int
    model: str = "discogs_multi"
    rating: int
    note: str | None = None


def context() -> tuple[Store, Settings]:
    settings = Settings.from_env()
    store = Store(settings.db_path)
    store.init()
    return store, settings


def create_job(kind: str, message: str) -> str:
    job_id = str(uuid4())
    with JOBS_LOCK:
        JOBS[job_id] = JobStatus(
            id=job_id,
            kind=kind,
            status="queued",
            message=message,
            started_at=perf_counter(),
        )
    logger.info("Created job job_id=%s kind=%s message=%s", job_id, kind, message)
    return job_id


def update_job(job_id: str, **changes: object) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        for key, value in changes.items():
            setattr(job, key, value)


def finish_job(job_id: str, status: str, message: str, error_detail: str | None = None) -> None:
    update_job(
        job_id,
        status=status,
        message=message,
        current=None,
        error_detail=error_detail,
        finished_at=perf_counter(),
    )
    log = logger.error if status == "failed" else logger.info
    log("Finished job job_id=%s status=%s message=%s", job_id, status, message)


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


def analysis_job_status_dict(job, store: Store | None = None) -> dict[str, object]:
    completed = job.done + job.failed
    started_at = timestamp_from_iso(job.created_at)
    finished_at = timestamp_from_iso(job.finished_at) if job.finished_at else None
    terminal = job.status not in {"queued", "running"}
    elapsed_until = finished_at or (timestamp_from_iso(job.updated_at) if terminal else datetime.now().timestamp())
    elapsed_seconds = max(0.0, elapsed_until - started_at) if started_at else 0.0
    tracks_per_min = (
        (completed / elapsed_seconds) * 60
        if job.status == "running" and elapsed_seconds > 0 and completed > 0
        else None
    )
    eta_seconds = None
    if tracks_per_min and job.total > completed and job.status == "running":
        eta_seconds = ((job.total - completed) / tracks_per_min) * 60
    task_summary = store.analysis_job_task_summary(job.id) if store is not None else {}
    workers = store.list_analysis_workers() if store is not None else []
    supporting_workers = [
        worker.worker_id
        for worker in workers
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
        for worker in workers
    ]
    status_hint = ""
    if job.status == "running":
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
    return {
        "worker_id": worker.worker_id,
        "models": [model for model in worker.models.split(",") if model],
        "status": worker.status,
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
) -> AnalysisTask:
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
    job = store.get_analysis_job(task.job_id)
    active = (
        task.status == "leased"
        and task.lease_owner == worker_id
        and job is not None
        and job.status == "running"
    )
    return {
        "task_id": task.id,
        "job_id": task.job_id,
        "status": task.status,
        "stage": task.stage,
        "lease_owner": task.lease_owner,
        "job_status": job.status if job is not None else None,
        "active": active,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.on_event("shutdown")
def shutdown_analyze_workers() -> None:
    global SHUTDOWN_REQUESTED
    logger.info("Application shutdown requested; terminating analyze workers")
    SHUTDOWN_REQUESTED = True
    with ANALYZE_EXECUTORS_LOCK:
        executors = list(ANALYZE_EXECUTORS)
    for executor in executors:
        terminate_process_pool(executor)


@app.get("/", response_class=HTMLResponse)
def test_ui() -> str:
    return UI_HTML


@app.get("/stats")
def stats(model: str = "discogs_multi") -> dict[str, object]:
    store, settings = context()
    head_model_names = [head.id for head in DISCOGS_EFFNET_HEADS]
    head_status = head_pack_status(store, settings)
    audio_status = audio_feature_status(store)
    return {
        "db": str(settings.db_path),
        "tracks": store.count_tracks(),
        "missing_files": store.count_missing_files(),
        "embeddings": store.count_embeddings(model),
        "missing_embeddings": store.count_missing_embeddings(model),
        "head_pack_expected_outputs": head_status["expected_outputs"],
        "head_pack_outputs": head_status["saved_outputs"],
        "head_pack_complete_tracks": head_status["complete_tracks"],
        "head_pack_missing_tracks": head_status["missing_tracks"],
        "missing_head_pack_tracks": store.count_tracks_missing_head_pack(head_model_names),
        "head_pack": head_status,
        "audio_features_complete_tracks": audio_status["complete_tracks"],
        "audio_features_missing_tracks": audio_status["missing_tracks"],
        "audio_features": audio_status,
        "model": model,
        "models": sorted(MODEL_FILES),
        "model_path": str(settings.model_path(model)),
        "model_exists": settings.model_path(model).exists(),
        "index": str(settings.index_path(model)),
        "index_exists": settings.index_path(model).exists(),
    }


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
    return {"results": [track_listing_dict(track) for track in tracks]}


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
    return track_dict(track)


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
        "track": track_dict(track),
        "outputs": outputs,
        "features": [feature_dict(feature) for feature in store.load_features(track_id)],
    }


@app.get("/tracks/{track_id}/audio")
def get_track_audio(track_id: int) -> FileResponse:
    store, _settings = context()
    track = store.get_track(track_id)
    if track is None:
        logger.warning("Audio requested for missing track track_id=%s", track_id)
        raise HTTPException(status_code=404, detail="Track not found")
    path = Path(track.path)
    if not path.exists() or not path.is_file():
        logger.warning("Audio file missing track_id=%s path=%s", track_id, path)
        store.mark_track_missing(track_id)
        raise HTTPException(status_code=410, detail="Audio file not mounted or no longer exists")
    store.mark_track_available(track_id)
    return FileResponse(path)


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
        "seed": track_dict(seed),
        "model": model,
        "results": [similar_track_dict(result) for result in results],
    }


@app.post("/jobs/scan")
def start_scan(request: ScanRequest, background_tasks: BackgroundTasks) -> dict[str, object]:
    job_id = create_job("scan", f"Waiting to scan {request.music_dir}")
    background_tasks.add_task(_scan_job, job_id, Path(request.music_dir))
    return {"status": "accepted", "job_id": job_id, "music_dir": request.music_dir}


@app.post("/jobs/analyze")
def start_analyze(request: AnalyzeRequest, background_tasks: BackgroundTasks) -> dict[str, object]:
    job_id = create_job("analyze", f"Waiting to analyze {request.model}")
    local_executor_enabled = request.execution_mode in {"both", "local"} and request.local_executor_enabled
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
        background_tasks.add_task(
            _analyze_job,
            job_id,
            request.model,
            request.limit,
            request.workers,
            request.tf_threads,
            True,
            request.max_attempts,
            False,
        )
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


@app.post("/workers/register")
def register_worker(request: WorkerRegisterRequest) -> dict[str, object]:
    store, _settings = context()
    store.expire_analysis_leases()
    store.register_analysis_worker(request.worker_id, request.models)
    return {"status": "ok", "worker_id": request.worker_id, "models": request.models}


@app.post("/workers/heartbeat")
def heartbeat_worker(request: WorkerRegisterRequest) -> dict[str, object]:
    return register_worker(request)


@app.get("/workers")
def list_workers() -> dict[str, object]:
    store, _settings = context()
    store.expire_analysis_leases()
    return {"workers": [analysis_worker_dict(worker) for worker in store.list_analysis_workers()]}


@app.post("/workers/claim")
def claim_worker_tasks(request: WorkerClaimRequest) -> dict[str, object]:
    store, _settings = context()
    store.expire_analysis_leases()
    store.register_analysis_worker(request.worker_id, request.models)
    tasks = store.claim_analysis_tasks(
        request.worker_id,
        request.models,
        limit=request.limit,
        lease_seconds=request.lease_seconds,
    )
    return {"tasks": [analysis_task_dict(task) for task in tasks]}


@app.get("/workers/tasks/{task_id}/audio")
def get_worker_task_audio(task_id: str) -> FileResponse:
    store, _settings = context()
    task = store.get_analysis_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != "leased":
        raise HTTPException(status_code=409, detail=f"Task is not active: {task.status}")
    job = store.get_analysis_job(task.job_id)
    if job is None or job.status != "running":
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
def submit_worker_results(request: WorkerSubmitRequest) -> dict[str, object]:
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
        )
        store.save_embedding(task.track_id, task.model_name, vector)
        store.mark_track_available(task.track_id)
        store.complete_analysis_task(task.id, request.worker_id)
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
        store.complete_analysis_task(task.id, request.worker_id)
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
        store.complete_analysis_task(task.id, request.worker_id)
        return task.id

    def accept_with_retry(operation):
        try:
            return sqlite_retry(operation)
        except sqlite3.OperationalError as exc:
            if is_sqlite_locked(exc):
                raise HTTPException(status_code=503, detail="SQLite is busy; retry submit") from exc
            raise

    accepted: list[str] = []
    rejected: list[dict[str, str]] = []
    for item in request.results:
        try:
            accepted.append(accept_with_retry(lambda item=item: accept_embedding(item)))
        except HTTPException:
            raise
        except Exception as exc:
            reject_task(item.task_id, exc, "result")
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
    sqlite_retry(
        lambda: store.update_analysis_worker(
            request.worker_id,
            stage="submitted",
            message=f"accepted {len(accepted)}, rejected {len(rejected)} result(s)",
            current_task_id=None,
        )
    )
    return {"status": "ok", "accepted": accepted, "rejected": rejected}


@app.post("/workers/failures")
def submit_worker_failures(request: WorkerFailuresRequest) -> dict[str, object]:
    store, _settings = context()
    store.expire_analysis_leases()
    store.update_analysis_worker(
        request.worker_id,
        stage="reporting_failures",
        message=f"reporting {len(request.failures)} failure(s)",
    )
    failed: list[str] = []
    for item in request.failures:
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
    job_id = create_job("download-head-models", "Waiting to download head models")
    background_tasks.add_task(_download_head_models_job, job_id)
    return {"status": "accepted", "job_id": job_id}


@app.post("/jobs/analyze-heads")
def start_analyze_heads(
    request: AnalyzeHeadsRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    job_id = create_job("analyze-heads", "Waiting to analyze Discogs-EffNet heads")
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
        background_tasks.add_task(
            _analyze_heads_job,
            job_id,
            request.limit,
            True,
            request.max_attempts,
            False,
        )
    return {
        "status": "accepted",
        "job_id": job_id,
        "limit": request.limit,
        "execution_mode": request.execution_mode,
        "local_executor_enabled": local_enabled,
    }


@app.post("/jobs/analyze-audio-features")
def start_analyze_audio_features(
    request: AnalyzeAudioFeaturesRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    job_id = create_job("analyze-audio-features", "Waiting to analyze audio features")
    local_enabled = request.execution_mode in {"both", "local"} and request.local_executor_enabled
    store, _settings = context()
    tracks = store.list_tracks_missing_features(AUDIO_FEATURE_EXTRACTOR, limit=request.limit)
    durable_job = store.create_analysis_job(
        AUDIO_FEATURE_EXTRACTOR,
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
        background_tasks.add_task(
            _analyze_audio_features_job,
            job_id,
            request.limit,
            True,
            request.max_attempts,
            False,
        )
    return {
        "status": "accepted",
        "job_id": job_id,
        "limit": request.limit,
        "execution_mode": request.execution_mode,
        "local_executor_enabled": local_enabled,
    }


@app.post("/jobs/analyze-genres")
def start_analyze_genres_compat(
    request: AnalyzeHeadsRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    return start_analyze_heads(request, background_tasks)


@app.post("/jobs/index")
def start_index(request: IndexRequest, background_tasks: BackgroundTasks) -> dict[str, object]:
    job_id = create_job("index", f"Waiting to build index for {request.model}")
    background_tasks.add_task(_index_job, job_id, request.model)
    return {"status": "accepted", "job_id": job_id, "model": request.model}


@app.post("/jobs/check-missing-files")
def start_check_missing_files(background_tasks: BackgroundTasks) -> dict[str, object]:
    job_id = create_job("check-missing-files", "Waiting to check file availability")
    background_tasks.add_task(_check_missing_files_job, job_id)
    return {"status": "accepted", "job_id": job_id}


@app.get("/jobs")
def list_jobs() -> dict[str, object]:
    store, _settings = context()
    store.expire_analysis_leases()
    durable_jobs = {job.id: analysis_job_status_dict(job, store) for job in store.recent_analysis_jobs(limit=20)}
    with JOBS_LOCK:
        now = perf_counter()
        jobs = []
        for job in JOBS.values():
            if job.id in durable_jobs:
                continue
            data = asdict(job)
            if job.status in {"queued", "running"}:
                data["elapsed_seconds"] = max(0.0, now - job.started_at)
            jobs.append(data)
    jobs.extend(durable_jobs.values())
    jobs.sort(key=lambda job: job["started_at"], reverse=True)
    return {
        "jobs": jobs[:20],
        "workers": [analysis_worker_dict(worker) for worker in store.list_analysis_workers()],
    }


@app.get("/jobs/{job_id}")
def get_job_detail(job_id: str) -> dict[str, object]:
    store, _settings = context()
    store.expire_analysis_leases()
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
        "job": analysis_job_status_dict(job, store),
        "tasks": [analysis_task_dict(task) for task in tasks],
    }


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: CancelJobRequest | None = None) -> dict[str, object]:
    reason = request.reason if request is not None else "Cancelled by user"
    store, _settings = context()
    durable_job = store.cancel_analysis_job(job_id, reason)
    with JOBS_LOCK:
        memory_job = JOBS.get(job_id)
        if memory_job is not None:
            memory_job.status = "cancelled"
            memory_job.message = reason
            memory_job.current = None
            memory_job.finished_at = perf_counter()
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


def _scan_job(job_id: str, music_dir: Path) -> None:
    try:
        if not music_dir.exists() or not music_dir.is_dir():
            logger.warning("Scan job music directory not found job_id=%s music_dir=%s", job_id, music_dir)
            finish_job(job_id, "failed", f"Music directory not found: {music_dir}")
            return
        store, _settings = context()
        logger.info("Starting scan job job_id=%s music_dir=%s", job_id, music_dir)
        update_job(job_id, status="running", message=f"Scanning {music_dir}", total=None)
        total = 0
        changed = 0
        for scanned in scan_music_folder(music_dir):
            _track_id, did_change = store.upsert_track(scanned)
            total += 1
            changed += int(did_change)
            update_job(job_id, done=total, current=str(scanned.path))
            if total % 25 == 0:
                update_job(job_id, done=total, message=f"Scanned {total} files")
        library_total = store.count_tracks()
        finish_job(
            job_id,
            "completed",
            f"Scanned {total} files, changed {changed}; library has {library_total} tracks",
        )
        update_job(job_id, done=total)
        logger.info(
            "Finished scan job job_id=%s music_dir=%s scanned=%s changed=%s library_total=%s",
            job_id,
            music_dir,
            total,
            changed,
            library_total,
        )
    except Exception as exc:
        logger.exception("Scan job failed job_id=%s music_dir=%s", job_id, music_dir)
        finish_job(job_id, "failed", str(exc))


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


_WORKER_EMBEDDER: DiscogsEffnetEmbedder | None = None


def configure_analyze_runtime(tf_threads: int) -> None:
    os.environ["TF_NUM_INTRAOP_THREADS"] = str(tf_threads)
    os.environ["TF_NUM_INTEROP_THREADS"] = "1"
    os.environ["OMP_NUM_THREADS"] = str(tf_threads)


def _init_embedding_worker(settings: Settings, model: str, tf_threads: int) -> None:
    global _WORKER_EMBEDDER
    configure_analyze_runtime(tf_threads)
    _WORKER_EMBEDDER = DiscogsEffnetEmbedder(settings, model)
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


def _extract_embedding_local(embedder: DiscogsEffnetEmbedder, track: Track) -> AnalyzeResult:
    try:
        vector = embedder.extract_track_vector(Path(track.path))
        return AnalyzeResult(task_id=None, track_id=track.id, path=track.path, status="ok", vector=vector)
    except Exception as exc:
        return AnalyzeResult(
            task_id=None,
            track_id=track.id,
            path=track.path,
            status="failed",
            **analyze_failure_fields(exc, embedding_failure_stage(exc)),
        )


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
    settings: Settings,
    model: str,
    workers: int,
    tf_threads: int,
):
    if SHUTDOWN_REQUESTED:
        return
    configure_analyze_runtime(tf_threads)
    if workers <= 1:
        embedder = DiscogsEffnetEmbedder(settings, model)
        for track in tracks:
            if SHUTDOWN_REQUESTED:
                return
            yield _extract_embedding_local(embedder, track)
        return

    executor = ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_embedding_worker,
        initargs=(settings, model, tf_threads),
        mp_context=multiprocessing.get_context("spawn"),
    )
    register_analyze_executor(executor)
    try:
        future_to_track = {
            executor.submit(_extract_embedding_worker, None, track.id, track.path): track
            for track in tracks
        }
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
    settings: Settings,
    model: str,
    workers: int,
    tf_threads: int,
):
    if SHUTDOWN_REQUESTED:
        return
    configure_analyze_runtime(tf_threads)
    if workers <= 1:
        embedder = DiscogsEffnetEmbedder(settings, model)
        for task in tasks:
            if SHUTDOWN_REQUESTED:
                return
            result = _extract_embedding_local(embedder, task_to_track(task))
            yield replace(result, task_id=task.id)
        return

    executor = ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_embedding_worker,
        initargs=(settings, model, tf_threads),
        mp_context=multiprocessing.get_context("spawn"),
    )
    register_analyze_executor(executor)
    try:
        future_to_task = {
            executor.submit(_extract_embedding_worker, task.id, task.track_id, task.path): task
            for task in tasks
        }
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
                if durable_job is None or durable_job.status == "completed":
                    break
                update_job(
                    job_id,
                    done=durable_job.done,
                    failed=durable_job.failed,
                    message=durable_job.message,
                    **analyze_progress(started_at, total, durable_job.done, durable_job.failed),
                )
                return
            for result in _iter_analyze_task_results(tasks, settings, model, workers, tf_threads):
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
                        retryable=result.error_type != "FileNotFoundError",
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
    except Exception as exc:
        analysis_logger.exception("Analyze job failed job_id=%s model=%s", job_id, model)
        finish_job(job_id, "failed", str(exc))


def _extract_heads_local(
    analyzer: DiscogsEffnetHeadPackAnalyzer,
    track: Track,
) -> HeadAnalyzeResult:
    try:
        outputs = analyzer.analyze_track(Path(track.path))
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
                if durable_job is None or durable_job.status == "completed":
                    break
                update_job(
                    job_id,
                    done=durable_job.done,
                    failed=durable_job.failed,
                    message=durable_job.message,
                    **analyze_progress(started_at, total, durable_job.done, durable_job.failed),
                )
                return
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
            result = _extract_heads_local(analyzer, track)
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
    track: Track,
) -> AudioFeaturesResult:
    try:
        features = analyzer.analyze_track(Path(track.path))
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


def _analyze_audio_features_job(
    job_id: str,
    limit: int | None,
    local_executor_enabled: bool = True,
    max_attempts: int = 3,
    enqueue: bool = True,
) -> None:
    try:
        if SHUTDOWN_REQUESTED:
            finish_job(job_id, "failed", "Audio feature analysis cancelled during application shutdown")
            return
        store, _settings = context()
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
        started_at = perf_counter()
        analysis_logger.info(
            "Starting analyze-audio-features job job_id=%s limit=%s total=%s extractor=%s",
            job_id,
            limit,
            total,
            AUDIO_FEATURE_EXTRACTOR,
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
        analyzer = AudioFeatureAnalyzer()
        local_worker_id = f"local-{job_id}"
        while True:
            if SHUTDOWN_REQUESTED:
                finish_job(job_id, "failed", "Audio feature analysis cancelled during application shutdown")
                return
            tasks = store.claim_analysis_tasks(
                local_worker_id,
                [AUDIO_FEATURE_EXTRACTOR],
                limit=1,
                lease_seconds=3600,
            )
            if not tasks:
                durable_job = store.get_analysis_job(job_id)
                if durable_job is None or durable_job.status == "completed":
                    break
                update_job(
                    job_id,
                    done=durable_job.done,
                    failed=durable_job.failed,
                    message=durable_job.message,
                    **analyze_progress(started_at, total, durable_job.done, durable_job.failed),
                )
                return
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
            update_job(job_id, current=track.path, message=f"Analyzing audio features for {track.path}")
            result = _extract_audio_features_local(analyzer, track)
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
    body { margin: 0; min-height: 100vh; background: var(--bg); color: var(--text); }
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
    .app { display: grid; grid-template-columns: 220px minmax(0, 1fr); min-height: 100vh; }
    aside { border-right: 1px solid var(--line); background: #111518; padding: 18px; }
    main { padding: 18px; display: grid; gap: 16px; align-content: start; }
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
    .actions { display: flex; gap: 8px; flex-wrap: wrap; }
    .stats { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 10px; }
    .stat { border: 1px solid var(--line); border-radius: 6px; padding: 12px; background: var(--panel); }
    .stat strong { display: block; font-size: 24px; }
    .stat span { color: var(--muted); font-size: 12px; }
    .layout { display: grid; grid-template-columns: minmax(320px, .9fr) minmax(360px, 1.1fr); gap: 16px; }
    .browse-layout { display: grid; grid-template-columns: minmax(240px, .55fr) minmax(420px, 1.45fr); gap: 16px; }
    .facet-group { display: grid; gap: 6px; margin-bottom: 12px; }
    .facet-list { display: grid; gap: 6px; max-height: 164px; overflow: auto; padding-right: 4px; }
    .facet-button { justify-content: space-between; text-align: left; width: 100%; min-height: 32px; overflow: hidden; }
    .facet-button.active { border-color: var(--accent); color: var(--accent); }
    .facet-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .basket { display: grid; gap: 8px; margin-top: 10px; }
    .rating-active { border-color: var(--accent); color: var(--accent); }
    .panel { border: 1px solid var(--line); border-radius: 8px; background: var(--panel); padding: 14px; min-width: 0; }
    .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .search { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; margin-bottom: 10px; }
    .list { display: grid; gap: 8px; max-height: 58vh; overflow: auto; padding-right: 4px; }
    .track {
      display: grid; gap: 4px; border: 1px solid var(--line); border-radius: 6px; padding: 10px;
      background: #14181b;
    }
    .track.selected { border-color: var(--accent); }
    .title { font-weight: 700; overflow-wrap: anywhere; }
    .meta { color: var(--muted); font-size: 13px; overflow-wrap: anywhere; }
    .path { color: #829096; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .model-table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }
    .model-table th, .model-table td { border-top: 1px solid var(--line); padding: 8px; text-align: left; vertical-align: top; }
    .model-table th { color: var(--muted); font-weight: 600; }
    .score { color: var(--accent-2); font-weight: 700; }
    .jobs { display: grid; gap: 8px; }
    .job { border: 1px solid var(--line); border-radius: 6px; padding: 10px; background: #14181b; }
    .job pre { white-space: pre-wrap; overflow-wrap: anywhere; margin: 6px 0 0; }
    .bar { height: 6px; background: #0b0d0f; border-radius: 999px; overflow: hidden; margin-top: 8px; }
    .fill { height: 100%; background: var(--accent); width: 0%; }
    .status-failed .fill { background: var(--bad); }
    .pill { display: inline-flex; align-items: center; min-height: 24px; padding: 0 8px; border-radius: 999px; background: var(--panel-2); color: var(--muted); font-size: 12px; }
    .bad-pill { border: 1px solid var(--bad); color: var(--bad); background: transparent; }
    .player { position: sticky; bottom: 0; border-top: 1px solid var(--line); background: #0f1316; padding: 12px 18px; display: grid; gap: 8px; }
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
      .stats, .layout, .browse-layout { grid-template-columns: 1fr; }
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
        <button data-nav="lostFiles" onclick="showSection('lostFiles')">Lost files</button>
        <button data-nav="erroredFiles" onclick="showSection('erroredFiles')">Errored files</button>
        <button data-nav="recommendations" onclick="showSection('recommendations')">Recommendations</button>
        <button data-nav="evaluation" onclick="showSection('evaluation')">Evaluation</button>
        <button data-nav="jobs" onclick="showSection('jobsPage')">Jobs</button>
        <button data-nav="settings" onclick="showSection('settings')">Settings</button>
      </nav>
    </aside>
    <main>
      <section id="dashboard" class="section active">
        <div class="stats">
          <div class="stat"><strong id="tracks">0</strong><span>tracks</span></div>
          <div class="stat"><strong id="missing">0</strong><span>need embeddings</span></div>
          <div class="stat"><strong id="missingHeadPackTracks">0</strong><span>need tags</span></div>
          <div class="stat"><strong id="missingAudioFeatures">0</strong><span>need audio features</span></div>
          <div class="stat"><strong id="missingFiles">0</strong><span>lost files</span></div>
          <div class="stat"><strong id="indexState">no</strong><span>index</span></div>
        </div>
        <div class="panel">
          <h2>Pipeline</h2>
          <div class="actions">
            <button id="scanBtn" onclick="startScan()">Scan</button>
            <button id="analyzeBtn" onclick="startAnalyze()">Analyze missing</button>
            <button id="analyzeHeadsBtn" onclick="startAnalyzeHeads()">Analyze Discogs-EffNet heads</button>
            <button id="analyzeAudioFeaturesBtn" onclick="startAnalyzeAudioFeatures()">Analyze audio features</button>
            <button id="indexBtn" onclick="startIndex()">Build index</button>
            <button class="primary" onclick="refreshAll()">Refresh</button>
          </div>
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
      <section id="library" class="section">
        <div class="panel">
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
          <div id="tracksList" class="list"></div>
        </div>
      </section>
      <section id="browse" class="section">
        <div class="browse-layout">
          <div class="panel">
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
          <div class="panel">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <h2>Tracks</h2>
              <span class="pill" id="browseFilterLabel">all tracks</span>
            </div>
            <div class="search">
              <input id="browseQuery" placeholder="Search within selected folder or tag">
              <button onclick="loadBrowseTracks()">Search</button>
            </div>
            <div id="browseTracks" class="list"></div>
          </div>
        </div>
      </section>
      <section id="lostFiles" class="section">
        <div class="panel">
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
          <div id="lostFilesList"></div>
        </div>
      </section>
      <section id="erroredFiles" class="section">
        <div class="panel">
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
          <div id="erroredFilesList"></div>
        </div>
      </section>
      <section id="recommendations" class="section">
        <div class="layout">
          <div class="panel">
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
            <div id="seedResults" class="list"></div>
          </div>
          <div class="panel">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <h2>Similar</h2>
              <button onclick="loadSimilar(seedId)" id="refreshSimilarBtn" disabled>Refresh similar</button>
            </div>
            <div id="similarList" class="list"></div>
          </div>
        </div>
      </section>
      <section id="evaluation" class="section">
        <div class="layout">
          <div class="panel">
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
            <div id="seedBasket" class="basket"></div>
          </div>
          <div class="panel">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <h2>Evaluate similar</h2>
              <button onclick="loadSimilar(seedId)" id="evaluationRefreshBtn" disabled>Refresh similar</button>
            </div>
            <div id="evaluationSeedPanel"></div>
            <div id="evaluationSimilarList" class="list" style="margin-top:10px"></div>
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
        <div class="panel">
          <h2>Workers</h2>
          <div id="workers" class="jobs"></div>
        </div>
      </section>
      <section id="settings" class="section">
        <div class="panel">
        <h2>Settings</h2>
        <label><span class="label-title">Music path in container <span class="info" tabindex="0" data-tooltip="Folder path visible to the app. Scan reads audio files from here; wrong mounts give an empty library.">(i)</span></span>
          <input id="musicDir" value="/music">
        </label>
        <label><span class="label-title">Model <span class="info" tabindex="0" data-tooltip="Embedding model used for analyze, index, and recommendations. Changing it requires separate embeddings and index.">(i)</span></span>
          <select id="model">
            <option value="discogs_multi">discogs_multi</option>
            <option value="discogs_track">discogs_track</option>
            <option value="discogs_label">discogs_label</option>
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
        <h3>Remote worker</h3>
        <label><span class="label-title">Server URL for worker <span class="info" tabindex="0" data-tooltip="Base URL that the remote machine can reach. Use the host/IP running this web app, not localhost unless the worker runs on the same machine.">(i)</span></span>
          <input id="workerServerUrl" value="http://127.0.0.1:8711" oninput="refreshWorkerCommand()">
        </label>
        <label><span class="label-title">Worker ID <span class="info" tabindex="0" data-tooltip="Stable name shown in Jobs / Workers. Use a different ID for each remote machine.">(i)</span></span>
          <input id="workerId" value="gpu-4090-1" oninput="refreshWorkerCommand()">
        </label>
        <label><span class="label-title">Claim batch size <span class="info" tabindex="0" data-tooltip="How many queued tasks the worker asks for in one claim. Higher values reduce API round trips.">(i)</span></span>
          <input id="workerClaimBatchSize" type="number" min="1" value="32" oninput="refreshWorkerCommand()">
        </label>
        <label><span class="label-title">Max in-flight tasks <span class="info" tabindex="0" data-tooltip="Maximum leased tasks held by the worker while it downloads and processes audio. Keep this high enough to avoid GPU starvation.">(i)</span></span>
          <input id="workerMaxInflightTasks" type="number" min="1" value="128" oninput="refreshWorkerCommand()">
        </label>
        <label><span class="label-title">Download concurrency <span class="info" tabindex="0" data-tooltip="How many source audio files the worker downloads at the same time. Useful on fast LAN storage.">(i)</span></span>
          <input id="workerDownloadConcurrency" type="number" min="1" value="8" oninput="refreshWorkerCommand()">
        </label>
        <label><span class="label-title">Submit batch size <span class="info" tabindex="0" data-tooltip="How many results/failures the worker sends back in one request.">(i)</span></span>
          <input id="workerSubmitBatchSize" type="number" min="1" value="32" oninput="refreshWorkerCommand()">
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
    <audio id="audioPlayer" controls preload="none"></audio>
  </div>
  <script>
    const SETTINGS_KEY = "discocs.settings.v1";
    const SETTINGS_FIELDS = [
      "musicDir", "model", "limit", "analyzeExecutionMode", "workers", "tfThreads",
      "workerServerUrl", "workerId", "workerClaimBatchSize", "workerMaxInflightTasks",
      "workerDownloadConcurrency", "workerSubmitBatchSize", "workerLeaseSeconds",
      "workerMaxTasksBeforeExit",
      "k", "maxPerArtist", "excludeSameAlbum"
    ];
    let seedId = null;
    let seedTrack = null;
    let activeTrackId = null;
    let lastJobs = [];
    let seedBasket = [];
    let evaluationIndex = -1;
    let browseFilters = {};
    let lostFilesPage = 1;
    const lostFilesPageSize = 50;
    let erroredFilesPage = 1;
    const erroredFilesPageSize = 50;
    function model() { return document.getElementById("model").value; }
    function text(value) { return value || ""; }
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
    async function json(url, options) {
      const response = await fetch(url, options);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || response.statusText);
      return data;
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
      const command = [
        "recs worker",
        "--server", shellQuote(server),
        "--worker-id", shellQuote(workerId),
        "--models", shellQuote(model()),
        "--models", "audio_features_v1",
        "--models", "discogs-effnet-heads",
        "--claim-batch-size", workerSetting("workerClaimBatchSize", "32"),
        "--max-inflight-tasks", workerSetting("workerMaxInflightTasks", "128"),
        "--download-concurrency", workerSetting("workerDownloadConcurrency", "8"),
        "--submit-batch-size", workerSetting("workerSubmitBatchSize", "32"),
        "--lease-seconds", workerSetting("workerLeaseSeconds", "900"),
        "--max-tasks-before-exit", workerSetting("workerMaxTasksBeforeExit", "0")
      ].join(" ");
      const target = document.getElementById("workerCommand");
      if (target) target.value = command;
    }
    function showSection(id) {
      document.querySelectorAll(".section").forEach(section => section.classList.toggle("active", section.id === id));
      document.querySelectorAll("nav button").forEach(button => button.classList.toggle("active", button.dataset.nav === id || (id === "jobsPage" && button.dataset.nav === "jobs")));
      if (id === "lostFiles") loadLostFiles();
      if (id === "erroredFiles") loadErroredFiles();
    }
    async function refreshStats() {
      const data = await json(`/stats?model=${encodeURIComponent(model())}`);
      document.getElementById("tracks").textContent = data.tracks;
      document.getElementById("missing").textContent = data.missing_embeddings;
      document.getElementById("missingHeadPackTracks").textContent = data.head_pack_missing_tracks;
      document.getElementById("missingAudioFeatures").textContent = data.audio_features_missing_tracks;
      document.getElementById("missingFiles").textContent = data.missing_files;
      document.getElementById("indexState").textContent = data.index_exists ? "yes" : "no";
      document.getElementById("modelState").textContent = `Model file: ${data.model_exists ? "ready" : "missing"} · ${data.model_path}`;
      renderHeadPackStatus(data.head_pack);
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
    function renderTrack(t, mode) {
      const emb = t.has_embedding ? "ready" : "missing";
      const tagBits = [text(t.genre), t.year ? String(t.year) : "", text(t.album)].filter(Boolean).join(" / ");
      const addSeedButton = ["browse", "library", "seed"].includes(mode)
        ? `<button onclick="addSeed(${t.id})">Add seed</button>` : "";
      return `<div class="track ${t.id === seedId ? "selected" : ""} ${t.id === activeTrackId ? "active-track" : ""}">
        <div class="row" style="justify-content:space-between">
          <div class="title">#${t.id} ${label(t)}</div>
          <div class="row">
            <button class="icon-button" onclick="openAnalysis(${t.id})" title="Analysis metadata" aria-label="Analysis metadata">
              <span class="icon-tablet" aria-hidden="true"></span>
            </button>
            <button onclick="playTrack(${t.id}, '${encodedArg(label(t))}')">Play</button>
            ${addSeedButton}
            <button onclick="setSeed(${t.id})">Seed</button>
          </div>
        </div>
        <div class="meta">${text(t.album)} ${t.duration ? `· ${Math.round(t.duration)}s` : ""} · embedding ${emb}</div>
        <div class="path" title="${t.path}">${t.path}</div>
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
            <td>#${item.track_id}<div class="meta">${esc(track)}</div></td>
            <td class="path" title="${esc(item.path)}">${esc(item.path)}</td>
            <td>${esc(item.model_name)}<div class="meta">${esc(item.job_kind)} · ${esc(item.status)} · attempt ${item.attempts}/${item.max_attempts}</div></td>
            <td>
              <pre class="meta" style="white-space:pre-wrap; margin:0">${esc(item.error)}</pre>
              <div class="meta">${esc(item.error_type || "")}${item.stage ? ` · ${esc(item.stage)}` : ""}</div>
            </td>
            <td>${formatDate(item.updated_at)}</td>
          </tr>`;
      }).join("");
      document.getElementById("erroredFilesList").innerHTML = `
        <table class="model-table">
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
      erroredFilesPage -= 1;
      await loadErroredFiles();
    }
    async function nextErroredFilesPage() {
      const data = await json(`/analysis/errors?page=${erroredFilesPage}&page_size=${erroredFilesPageSize}`);
      if (erroredFilesPage >= data.pages) return;
      erroredFilesPage += 1;
      await loadErroredFiles();
    }
    function toggleLostFilesSelection(checked) {
      document.querySelectorAll(".lost-checkbox").forEach(input => { input.checked = checked; });
    }
    async function previousLostFilesPage() {
      if (lostFilesPage <= 1) return;
      lostFilesPage -= 1;
      await loadLostFiles();
    }
    async function nextLostFilesPage() {
      const data = await json(`/lost-files?page=${lostFilesPage}&page_size=${lostFilesPageSize}`);
      if (lostFilesPage >= data.pages) return;
      lostFilesPage += 1;
      await loadLostFiles();
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
    async function searchTracks() {
      const q = document.getElementById("query").value;
      const status = document.getElementById("embeddingStatus").value;
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
    async function refreshBrowse() {
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
    async function selectEvaluationSeed(index) {
      if (index < 0 || index >= seedBasket.length) return;
      evaluationIndex = index;
      seedId = seedBasket[index].id;
      seedTrack = seedBasket[index];
      document.getElementById("evaluationSeedPanel").innerHTML = renderTrack({...seedTrack, has_embedding: true}, "evaluation");
      document.getElementById("evaluationRefreshBtn").disabled = false;
      renderSeedBasket();
      await loadSimilar(seedId);
    }
    async function nextEvaluationSeed() {
      if (!seedBasket.length) return;
      const next = Math.min(evaluationIndex + 1, seedBasket.length - 1);
      await selectEvaluationSeed(next);
    }
    async function skipEvaluationSeed() {
      await nextEvaluationSeed();
    }
    async function setSeed(id) {
      seedId = id;
      seedTrack = await json(`/tracks/${id}`);
      document.getElementById("seedPanel").innerHTML = renderTrack({...seedTrack, has_embedding: true}, "seed");
      document.getElementById("refreshSimilarBtn").disabled = false;
      showSection("recommendations");
      await loadSimilar(id);
      await searchTracks();
    }
    async function loadSimilar(id) {
      if (!id) return;
      const k = document.getElementById("k").value;
      const max = document.getElementById("maxPerArtist").value;
      const exclude = document.getElementById("excludeSameAlbum").checked;
      const data = await json(`/tracks/${id}/similar?model=${encodeURIComponent(model())}&k=${k}&max_per_artist=${max}&exclude_same_album=${exclude}`);
      const html = data.results.map(t => `
        <div class="track ${t.id === activeTrackId ? "active-track" : ""}">
          <div class="row" style="justify-content:space-between">
            <div class="title"><span class="score">${t.similarity.toFixed(3)}</span> #${t.id} ${label(t)}</div>
            <button onclick="playTrack(${t.id}, '${encodedArg(label(t))}')">Play</button>
          </div>
          <div class="meta">${[text(t.genre), t.year || "", text(t.album)].filter(Boolean).join(" / ")}</div>
          <div class="path" title="${t.path}">${t.path}</div>
          <div class="row">
            <button class="${t.rating === 3 ? "rating-active" : ""}" onclick="rate(${t.id}, 3)">good</button>
            <button class="${t.rating === 2 ? "rating-active" : ""}" onclick="rate(${t.id}, 2)">okay</button>
            <button class="${t.rating === 0 ? "rating-active" : ""}" onclick="rate(${t.id}, 0)">bad</button>
          </div>
        </div>`).join("");
      document.getElementById("similarList").innerHTML = html;
      document.getElementById("evaluationSimilarList").innerHTML = html;
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
      await searchTracks();
      if (seedId) await loadSimilar(seedId);
    }
    async function rate(resultId, rating) {
      if (!seedId) return;
      await json("/feedback", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({seed_track_id: seedId, result_track_id: resultId, model: model(), rating})
      });
      await loadSimilar(seedId);
    }
    async function startScan() {
      await json("/jobs/scan", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({music_dir: document.getElementById("musicDir").value})
      });
      await refreshJobs();
    }
    async function startAnalyze() {
      const rawLimit = document.getElementById("limit").value;
      const rawWorkers = document.getElementById("workers").value;
      const rawTfThreads = document.getElementById("tfThreads").value;
      const executionMode = document.getElementById("analyzeExecutionMode").value;
      const parsedLimit = rawLimit ? Number(rawLimit) : null;
      await json("/jobs/analyze", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          model: model(),
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
      const executionMode = document.getElementById("analyzeExecutionMode").value;
      const parsedLimit = rawLimit ? Number(rawLimit) : null;
      await json("/jobs/analyze-audio-features", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          limit: parsedLimit && parsedLimit > 0 ? parsedLimit : null,
          execution_mode: executionMode,
          local_executor_enabled: executionMode !== "remote"
        })
      });
      await refreshJobs();
    }
    async function startIndex() {
      await json("/jobs/index", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({model: model()})
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
    async function loadJobDetail(jobId) {
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
      showSection("jobsPage");
    }
    async function refreshJobs() {
      const data = await json("/jobs");
      lastJobs = data.jobs;
      const running = new Set(data.jobs.filter(job => ["queued", "running"].includes(job.status)).map(job => job.kind));
      document.getElementById("scanBtn").disabled = running.has("scan");
      document.getElementById("analyzeBtn").disabled = running.has("analyze");
      document.getElementById("analyzeHeadsBtn").disabled = running.has("analyze-heads");
      document.getElementById("downloadHeadsBtn").disabled = running.has("download-head-models");
      document.getElementById("analyzeAudioFeaturesBtn").disabled = running.has("analyze-audio-features");
      document.getElementById("checkMissingBtn").disabled = running.has("check-missing-files");
      document.getElementById("indexBtn").disabled = running.has("index");
      const html = data.jobs.map(job => {
        const total = job.total || 0;
        const percent = total ? Math.round(((job.done + job.failed) / total) * 100) : (job.status === "completed" ? 100 : 0);
        const terminal = !["queued", "running"].includes(job.status);
        const elapsed = job.elapsed_seconds ? `${Math.round(job.elapsed_seconds)}s ${terminal ? "duration" : "elapsed"}` : "";
        const rate = job.tracks_per_min ? `${job.tracks_per_min.toFixed(1)} tracks/min` : "";
        const eta = job.eta_seconds ? `${Math.round(job.eta_seconds)}s ETA` : "";
        const timing = [elapsed, rate, eta].filter(Boolean).join(" - ");
        const startedAt = job.created_at || (job.started_at ? new Date(job.started_at * 1000).toLocaleString() : "");
        const updatedAt = job.updated_at || "";
        const finishedAt = job.finished_at_iso || "";
        const workerLine = (job.leased_workers || []).length
          ? `workers: ${(job.leased_workers || []).map(item => `${text(item.worker_id)}(${item.count})`).join(", ")}`
          : "";
        const breakdown = (job.status_breakdown || []).map(item => `${text(item.status)}${item.stage ? `/${text(item.stage)}` : ""}: ${item.count}`).join(", ");
        const oldestLease = job.oldest_lease ? `oldest lease: ${text(job.oldest_lease.worker_id || "")}, ${Math.round(job.oldest_lease_age || 0)}s, ${text(job.oldest_lease.stage || "")}` : "";
        const canCancel = ["queued", "running"].includes(job.status);
        return `<div class="job status-${job.status}">
          <div class="row" style="justify-content:space-between">
            <strong>${job.kind}</strong>
            <div class="row">
              <button onclick="loadJobDetail('${job.id}')">Details</button>
              ${canCancel ? `<button onclick="cancelJob('${job.id}')">Cancel</button>` : ""}
              <span class="pill">${job.status}</span>
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
      document.getElementById("jobs").innerHTML = html;
      document.getElementById("dashboardJobs").innerHTML = html || `<div class="meta">No jobs yet</div>`;
      const workerHtml = (data.workers || []).map(worker => `
        <div class="job status-${worker.status}">
          <div class="row" style="justify-content:space-between">
            <strong>${text(worker.worker_id)}</strong><span class="pill">${text(worker.status)}</span>
          </div>
          <div class="meta">${text((worker.models || []).join(", "))}</div>
          <div class="meta">${text(worker.stage || "idle")}${worker.message ? ` - ${text(worker.message)}` : ""}</div>
          <div class="meta">claimed ${worker.claimed_count || 0}, completed ${worker.completed_count || 0}, failed ${worker.failed_count || 0}, released ${worker.released_count || 0}</div>
          ${worker.current_task_id ? `<div class="meta">current: ${text(worker.current_task_id)}</div>` : ""}
          <div class="meta">last seen: ${text(worker.last_seen_at || "")}</div>
        </div>`).join("");
      document.getElementById("workers").innerHTML = workerHtml || `<div class="meta">No workers seen yet</div>`;
    }
    async function refreshAll() {
      await refreshStats();
      await searchTracks();
      await refreshBrowse();
      await loadLostFiles();
      await refreshJobs();
      renderSeedBasket();
    }
    document.getElementById("model").addEventListener("change", refreshAll);
    document.getElementById("model").addEventListener("change", refreshWorkerCommand);
    document.getElementById("query").addEventListener("keydown", event => {
      if (event.key === "Enter") searchTracks();
    });
    document.getElementById("seedQuery").addEventListener("keydown", event => {
      if (event.key === "Enter") searchSeeds();
    });
    document.getElementById("browseQuery").addEventListener("keydown", event => {
      if (event.key === "Enter") loadBrowseTracks();
    });
    document.getElementById("audioPlayer").addEventListener("error", () => {
      document.getElementById("playerError").textContent = "file not mounted";
    });
    loadSettings();
    bindSettingsAutosave();
    refreshWorkerCommand();
    refreshAll();
    setInterval(() => { refreshStats(); refreshJobs(); }, 2500);
  </script>
</body>
</html>
"""
