"""Analysis pipeline helpers — dict builders, sqlite utilities, decode helpers.

Extracted from app/main.py — Stage 6e.
"""
from __future__ import annotations

import base64
import json
import logging
import socket
import sqlite3
import time
import traceback
from datetime import UTC, datetime
from time import perf_counter
from typing import TYPE_CHECKING

import numpy as np
from fastapi import HTTPException

from app.audio_features import AUDIO_FEATURE_EXTRACTOR
from app.head_pack import DISCOGS_EFFNET_HEADS, head_pack_readiness
from app.recommender import index_metadata_path
from app.state import WORKER_CONNECTED_TTL_SECONDS

if TYPE_CHECKING:
    from app.config import Settings
    from app.models import AnalysisTask, Track
    from app.schemas.requests import WorkerHeadOutputItem, WorkerResultItem
    from app.store import Store

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exception / traceback utilities
# ---------------------------------------------------------------------------

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


def download_failure_hint(exc: Exception) -> str | None:
    reason = getattr(exc, "reason", None)
    if isinstance(reason, socket.gaierror):
        return (
            "DNS lookup failed in the server runtime. Check network/DNS access from the "
            "machine or container running discocs, or place the model files in models/ manually."
        )
    return None


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Progress / status helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Dict serializers
# ---------------------------------------------------------------------------

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
        "audio_url": f"/api/v1/workers/tasks/{task.id}/audio",
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


# ---------------------------------------------------------------------------
# Worker result decoders
# ---------------------------------------------------------------------------

def decode_worker_vector(item: WorkerResultItem) -> np.ndarray:
    if item.dtype != "float32":
        raise ValueError("Only float32 worker vectors are supported")
    try:
        raw = base64.b64decode(item.vector_b64, validate=True)
    except ValueError as exc:
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
    except ValueError as exc:
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
