"""Worker registration, task claim/submit/release API routes.

Extracted from app/main.py — Stage 6e.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.analysis_helpers import (
    analysis_task_dict,
    analysis_worker_dict,
    decode_worker_scores,
    decode_worker_vector,
    is_sqlite_locked,
    raise_worker_sqlite_http_exception,
    sqlite_retry,
    validate_worker_task_identity,
)
from app.analysis_jobs import (
    schedule_auto_index_for_analysis,
)
from app.api.deps import context
from app.audio_source import has_navidrome_audio_source, track_audio_path
from app.models import TrackFeature, TrackPrediction
from app.schemas.requests import (
    WorkerClaimRequest,
    WorkerFailuresRequest,
    WorkerRegisterRequest,
    WorkerReleaseRequest,
    WorkerSubmitRequest,
)
from app.state import WORKER_HEARTBEAT_WRITE_INTERVAL_SECONDS

logger = logging.getLogger(__name__)

router = APIRouter()

_TASK_NOT_FOUND = "Task not found"
_SQLITE_BUSY_RETRY_SUBMIT = "SQLite is busy; retry submit"


def _audio_response_media_type(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix == ".flac":
        return "audio/flac"
    if suffix == ".mp3":
        return "audio/mpeg"
    if suffix in {".m4a", ".mp4", ".aac"}:
        return "audio/mp4"
    if suffix == ".ogg":
        return "audio/ogg"
    if suffix == ".opus":
        return "audio/opus"
    if suffix == ".wav":
        return "audio/wav"
    return None


@router.get("/workers/tasks/{task_id}/state")
def get_worker_task_state(task_id: str, worker_id: str) -> dict[str, object]:
    store, _settings = context()
    task = store.get_analysis_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=_TASK_NOT_FOUND)
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


@router.post("/workers/register")
def register_worker(request: WorkerRegisterRequest) -> dict[str, object]:
    store, _settings = context()
    try:
        sqlite_retry(store.expire_analysis_leases)
        sqlite_retry(lambda: store.register_analysis_worker(request.worker_id, request.models))
    except sqlite3.OperationalError as exc:
        raise_worker_sqlite_http_exception(exc, "register")
    return {"status": "ok", "worker_id": request.worker_id, "models": request.models}


@router.post("/workers/heartbeat")
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


@router.get("/workers")
def list_workers() -> dict[str, object]:
    store, _settings = context()
    return {"workers": [analysis_worker_dict(worker) for worker in store.list_analysis_workers()]}


@router.post("/workers/claim")
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


@router.get("/workers/tasks/{task_id}/audio")
def get_worker_task_audio(task_id: str) -> FileResponse:
    store, settings = context()
    task = store.get_analysis_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=_TASK_NOT_FOUND)
    if task.status != "leased":
        raise HTTPException(status_code=409, detail=f"Task is not active: {task.status}")
    job_status = store.get_analysis_job_status(task.job_id)
    if job_status != "running":
        raise HTTPException(status_code=409, detail="Task job is not running")
    track = store.get_track(task.track_id)
    if track is None:
        store.fail_analysis_task(
            task_id, error="Track not found", error_type="LookupError",
            stage="audio", retryable=False,
        )
        raise HTTPException(status_code=404, detail="Track not found")
    if track.file_size != task.file_size or track.mtime != task.mtime:
        store.fail_analysis_task(
            task_id, error="Track changed after task was created", error_type="StaleTaskError",
            stage="audio", retryable=False,
        )
        raise HTTPException(status_code=409, detail="Task file identity is stale")
    if has_navidrome_audio_source(store, track):
        try:
            manager = track_audio_path(store, settings, track)
            path = manager.__enter__()
        except Exception as exc:
            store.fail_analysis_task(
                task_id, error=str(exc), error_type=type(exc).__name__,
                stage="navidrome-download", worker_id=task.lease_owner, retryable=True,
            )
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return FileResponse(
            path,
            media_type=_audio_response_media_type(path),
            background=BackgroundTask(manager.__exit__, None, None, None),
        )
    path = Path(track.path)
    if not path.exists() or not path.is_file():
        store.mark_track_missing(track.id)
        store.fail_analysis_task(
            task_id, error=f"Audio file not found: {track.path}",
            error_type="FileNotFoundError", stage="audio", retryable=False,
        )
        raise HTTPException(status_code=410, detail="Audio file not mounted or no longer exists")
    return FileResponse(path, media_type=_audio_response_media_type(path))


@router.post("/workers/results")
def submit_worker_results(
    request: WorkerSubmitRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    from time import perf_counter
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
            raise HTTPException(status_code=503, detail=_SQLITE_BUSY_RETRY_SUBMIT) from exc
        raise

    def reject_task(task_id: str, exc: Exception, log_label: str) -> None:
        if is_sqlite_locked(exc):
            raise HTTPException(status_code=503, detail=_SQLITE_BUSY_RETRY_SUBMIT) from exc
        rejected.append({"task_id": task_id, "error": str(exc)})
        try:
            sqlite_retry(
                lambda: store.fail_analysis_task(
                    task_id, error=str(exc), error_type=type(exc).__name__,
                    stage="submit", worker_id=request.worker_id, retryable=False,
                )
            )
        except sqlite3.OperationalError as lock_exc:
            if is_sqlite_locked(lock_exc):
                raise HTTPException(status_code=503, detail=_SQLITE_BUSY_RETRY_SUBMIT) from lock_exc
            raise
        except Exception:
            logger.exception("Failed to mark worker %s rejected task_id=%s", log_label, task_id)

    completed_embedding_job_ids: set[str] = set()
    completed_job_ids: set[str] = set()

    def accept_embedding(item) -> str:
        vector = decode_worker_vector(item)
        task = validate_worker_task_identity(
            store, item.task_id, request.worker_id, item.track_id, item.model_name,
            item.file_size, item.mtime, expire_leases=False,
        )
        store.save_embedding(task.track_id, task.model_name, vector)
        store.mark_track_available(task.track_id)
        store.complete_analysis_task(task.id, request.worker_id, refresh_job=False, update_worker=False)
        completed_embedding_job_ids.add(task.job_id)
        completed_job_ids.add(task.job_id)
        return task.id

    def accept_features(item) -> str:
        task = validate_worker_task_identity(
            store, item.task_id, request.worker_id, item.track_id, item.model_name,
            item.file_size, item.mtime, expire_leases=False,
        )
        features = [
            TrackFeature(
                name=feature.name, value=feature.value, text_value=feature.text_value,
                unit=feature.unit, confidence=feature.confidence, extractor=feature.extractor,
            )
            for feature in item.features
        ]
        store.save_features(task.track_id, features)
        store.mark_track_available(task.track_id)
        store.complete_analysis_task(task.id, request.worker_id, refresh_job=False, update_worker=False)
        completed_job_ids.add(task.job_id)
        return task.id

    def accept_heads(item) -> str:
        task = validate_worker_task_identity(
            store, item.task_id, request.worker_id, item.track_id, item.model_name,
            item.file_size, item.mtime, expire_leases=False,
        )
        for output in item.outputs:
            scores = decode_worker_scores(output)
            store.save_model_output(task.track_id, output.model_name, scores, output.aggregation)
            store.save_predictions(
                task.track_id, output.model_name,
                [
                    TrackPrediction(label=prediction.label, score=prediction.score, rank=prediction.rank)
                    for prediction in output.predictions
                ],
            )
        store.mark_track_available(task.track_id)
        store.complete_analysis_task(task.id, request.worker_id, refresh_job=False, update_worker=False)
        completed_job_ids.add(task.job_id)
        return task.id

    from app.models import utc_now

    def accept_feature_batch(items) -> list[str]:
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
                    raise ValueError(_TASK_NOT_FOUND)
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
                            item.track_id, feature.name, feature.value, feature.text_value,
                            feature.unit, feature.confidence, feature.extractor, now,
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
                raise HTTPException(status_code=503, detail=_SQLITE_BUSY_RETRY_SUBMIT) from exc
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
                request.worker_id, len(request.feature_results),
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
            len(request.results), len(request.feature_results), len(request.head_results),
            len(accepted), len(rejected), accept_seconds, refresh_seconds, total_seconds,
        )
    return {"status": "ok", "accepted": accepted, "rejected": rejected}


@router.post("/workers/failures")
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
            item.task_id, error=item.error, error_type=item.error_type,
            stage=item.stage, worker_id=request.worker_id, retryable=item.retryable,
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


@router.post("/workers/release")
def release_worker_tasks(request: WorkerReleaseRequest) -> dict[str, object]:
    store, _settings = context()
    store.expire_analysis_leases()
    released = store.release_analysis_tasks(request.worker_id, request.task_ids)
    return {"status": "ok", "released": released}
