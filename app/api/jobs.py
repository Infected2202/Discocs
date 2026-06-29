"""Jobs, models, stats, index, and feedback API routes.

Extracted from app/main.py — Stage 6e.
"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict
from time import perf_counter

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from app.analysis_helpers import (
    analysis_error_count,
    analysis_job_status_dict,
    analysis_task_dict,
    analysis_worker_dict,
    audio_feature_status,
    head_pack_status,
    recommender_index_status,
)
from app.analysis_jobs import (
    _analyze_audio_features_job,
    _analyze_heads_job,
    _analyze_job,
    _check_missing_files_job,
    _download_head_models_job,
    _index_job,
    _navidrome_sync_job,
    schedule_auto_index_for_analysis,
)
from app.api.deps import context
from app.audio_features import AUDIO_FEATURE_EXTRACTOR
from app.config import DISCOGS_EFFNET_MODEL, MODEL_FILES, MUQ_MULAN_MODEL
from app.head_pack import DISCOGS_EFFNET_HEADS, download_head_pack_models, head_pack_readiness
from app.recommender import build_index
from app.schemas.requests import (
    AnalyzeAudioFeaturesRequest,
    AnalyzeHeadsRequest,
    AnalyzeRequest,
    CancelJobRequest,
    FeedbackRequest,
    GeneratedMixSettingsRequest,
    IndexRequest,
    NavidromeSyncRequest,
)
from app.services.release_aggregates import run_release_aggregate_job
from app.services.release_scoring import release_recommendation_defaults
from app.services.jobs import (
    create_deferred_job_if_busy,
    create_job,
    finish_job,
    maybe_start_next_deferred_job,
    sync_memory_jobs_from_durable_jobs,
    update_job,
)
from app.state import (
    ACTIVE_JOB_STATUSES,
    DEFAULT_ANALYZE_TF_THREADS,
    DEFAULT_ANALYZE_WORKERS,
    DEFERRED_JOB_ORDER,
    DEFERRED_JOB_STARTERS,
    DEFERRED_JOBS_LOCK,
    JOBS,
    JOBS_LOCK,
    STATS_CACHE,
    STATS_CACHE_LOCK,
    STATS_CACHE_TTL_SECONDS,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Stats cache helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Stats + head-pack routes
# ---------------------------------------------------------------------------

@router.get("/stats")
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
    agg_ready = store.count_release_aggregates(model)
    agg_pending = store.count_releases_needing_aggregation(model)
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
        "release_aggregates": {"ready": agg_ready, "pending": agg_pending},
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


@router.get("/models/head-pack")
def get_head_pack() -> dict[str, object]:
    store, settings = context()
    return head_pack_status(store, settings)


# ---------------------------------------------------------------------------
# Job launch routes
# ---------------------------------------------------------------------------

@router.post("/jobs/analyze")
def start_analyze(request: AnalyzeRequest, background_tasks: BackgroundTasks) -> dict[str, object]:
    def start_now(job_id: str, tasks: BackgroundTasks | None) -> dict[str, object]:
        local_executor_enabled = request.execution_mode in {"both", "local"} and request.local_executor_enabled
        if request.model == MUQ_MULAN_MODEL:
            local_executor_enabled = False
        try:
            store, _settings = context()
            durable_job = store.create_analysis_job(
                request.model, request.limit, kind="analyze",
                local_executor_enabled=local_executor_enabled,
                workers=request.workers, tf_threads=request.tf_threads,
                max_attempts=request.max_attempts, job_id=job_id,
            )
        except Exception:
            logger.exception("Failed to create analysis job job_id=%s model=%s", job_id, request.model)
            finish_job(job_id, "failed", f"Failed to create analysis job for {request.model}")
            raise
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
            args = (job_id, request.model, request.limit, request.workers, request.tf_threads, True, request.max_attempts, False)
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


@router.post("/models/download-head-pack")
def download_head_pack() -> dict[str, object]:
    _store, settings = context()
    results = download_head_pack_models(settings)
    return {
        "status": "ok",
        "downloaded": [str(result.path) for result in results if result.downloaded],
        "already_present": [str(result.path) for result in results if not result.downloaded],
        "head_pack": head_pack_readiness(settings),
    }


@router.post("/jobs/download-head-models")
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


@router.post("/jobs/analyze-heads")
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
            "discogs-effnet-heads", request.limit, kind="analyze-heads",
            tracks=tracks, local_executor_enabled=local_enabled,
            max_attempts=request.max_attempts, job_id=job_id,
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


@router.post("/jobs/analyze-audio-features")
def start_analyze_audio_features(
    request: AnalyzeAudioFeaturesRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    extractor = request.extractor.strip() or AUDIO_FEATURE_EXTRACTOR
    if extractor != AUDIO_FEATURE_EXTRACTOR:
        raise HTTPException(status_code=400, detail=f"Unsupported audio feature extractor: {extractor}")

    def start_now(job_id: str, tasks: BackgroundTasks | None) -> dict[str, object]:
        local_enabled = request.execution_mode in {"both", "local"} and request.local_executor_enabled
        store, _settings = context()
        if request.reset_existing:
            tracks = store.list_active_tracks(limit=request.limit)
            deleted_features = store.delete_features_for_tracks([track.id for track in tracks], extractor)
            logger.info(
                "Reset audio features before analysis tracks=%s deleted_features=%s extractor=%s",
                len(tracks), deleted_features, extractor,
            )
        else:
            tracks = store.list_tracks_missing_features(extractor, limit=request.limit)
            deleted_features = 0
        clear_stats_cache()
        durable_job = store.create_analysis_job(
            extractor, request.limit, kind="analyze-audio-features",
            tracks=tracks, local_executor_enabled=local_enabled,
            max_attempts=request.max_attempts, job_id=job_id,
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


@router.post("/jobs/analyze-genres")
def start_analyze_genres_compat(
    request: AnalyzeHeadsRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    return start_analyze_heads(request, background_tasks)


@router.post("/jobs/index")
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


@router.post("/jobs/check-missing-files")
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


@router.post("/jobs/navidrome-sync")
def start_navidrome_sync(
    request: NavidromeSyncRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    def start_now(job_id: str, tasks: BackgroundTasks | None) -> dict[str, object]:
        store, _settings = context()
        known_total = request.limit or store.count_external_tracks("navidrome") or 0
        job = store.create_progress_job(
            "navidrome-sync", "navidrome",
            total=known_total, message="Waiting to sync Navidrome catalog", job_id=job_id,
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


# ---------------------------------------------------------------------------
# Job listing / detail / cancel
# ---------------------------------------------------------------------------

@router.get("/jobs")
def list_jobs(
    include_completed: bool = False,
    detail: bool = False,
    include_workers: bool = False,
) -> dict[str, object]:
    from time import perf_counter
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


@router.get("/jobs/{job_id}")
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


@router.post("/jobs/{job_id}/cancel")
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
            from time import perf_counter
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


# ---------------------------------------------------------------------------
# Index rebuild + feedback
# ---------------------------------------------------------------------------

@router.post("/index/rebuild")
def rebuild_index(request: IndexRequest) -> dict[str, object]:
    store, settings = context()
    try:
        logger.info("Rebuilding index via API model=%s", request.model)
        path = build_index(store, settings, request.model)
    except ValueError as exc:
        logger.warning("Index rebuild failed model=%s error=%s", request.model, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "model": request.model, "index": str(path)}


@router.post("/api/v1/jobs/release-aggregates")
def api_v1_release_aggregates_job(
    model_name: str = Query(default="discogs_multi"),
    limit: int = Query(default=0, ge=0, description="Max releases to process (0 = all)"),
) -> dict[str, object]:
    """Trigger the release aggregate computation job.

    Creates a tracked JobStatus entry and runs in a daemon thread.
    Returns immediately with the job_id and status envelope.
    """
    from threading import Thread
    store, _settings = context()
    pending = store.count_releases_needing_aggregation(model_name)
    if pending == 0:
        return {"status": "ok", "message": "All releases already aggregated", "pending": 0, "model_name": model_name}

    job_id = create_job("release_aggregates", f"Computing aggregates for {pending} releases (model={model_name})")

    def _run() -> None:
        run_release_aggregate_job(store, model_name, limit=limit, job_id=job_id)

    Thread(target=_run, daemon=True, name=f"release-agg-{job_id[:8]}").start()
    return {
        "status": "started",
        "job_id": job_id,
        "message": f"Release aggregate job started for {pending} releases",
        "pending": pending,
        "model_name": model_name,
    }


@router.get("/api/v1/albums/settings")
def api_v1_album_recommendation_settings() -> dict[str, object]:
    """Return default album recommendation settings."""
    return {"settings": release_recommendation_defaults()}


@router.get("/api/v1/jobs/release-aggregates/status")
def api_v1_release_aggregates_status(
    model_name: str = Query(default="discogs_multi"),
) -> dict[str, object]:
    """Return current aggregate coverage for a model."""
    store, _settings = context()
    ready = store.count_release_aggregates(model_name)
    pending = store.count_releases_needing_aggregation(model_name)
    return {
        "model_name": model_name,
        "ready": ready,
        "pending": pending,
    }


@router.post("/api/v1/jobs/albums-for-you")
def api_v1_albums_for_you_job(
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    """Recompute the Albums For You recommendation cache."""
    store, settings = context()
    model_name = settings.default_model

    def _run() -> None:
        from app.services.albums_for_you import refresh_albums_for_you  # noqa: PLC0415
        from app.services.release_similarity import invalidate_cache  # noqa: PLC0415
        invalidate_cache(model_name)
        count = refresh_albums_for_you(store, model_name)
        logger.info("albums_for_you manual refresh done count=%d model=%s", count, model_name)

    background_tasks.add_task(_run)
    age = store.albums_for_you_cache_age_hours(model_name)
    return {
        "status": "started",
        "model_name": model_name,
        "cache_age_hours": age,
    }


@router.get("/api/v1/jobs/albums-for-you/status")
def api_v1_albums_for_you_status() -> dict[str, object]:
    store, settings = context()
    model_name = settings.default_model
    age = store.albums_for_you_cache_age_hours(model_name)
    cached = store.get_albums_for_you_cache(model_name)
    import json  # noqa: PLC0415
    count = len(json.loads(cached)) if cached else 0
    return {
        "model_name": model_name,
        "cached_count": count,
        "cache_age_hours": age,
    }


@router.post("/api/v1/jobs/flow-profile")
def api_v1_flow_profile_rebuild_job(
    background_tasks: BackgroundTasks,
    model_key: str = Query(default="discogs_multi"),
) -> dict[str, object]:
    """Rebuild the Flow taste profile (seed collection + clustering)."""
    store, settings = context()

    def _run_rebuild() -> None:
        from app.services.flow_regions import FlowSettings, rebuild_flow_profile
        fs = FlowSettings(model_key=model_key)
        try:
            summary = rebuild_flow_profile(store, settings, fs)
            logger.info("Flow profile rebuild complete: %s", summary)
        except Exception:
            logger.exception("Flow profile rebuild failed model=%s", model_key)

    background_tasks.add_task(_run_rebuild)
    return {"status": "started", "model_key": model_key}


@router.get("/api/v1/jobs/flow-profile/status")
def api_v1_flow_profile_status(
    model_key: str = Query(default="discogs_multi"),
) -> dict[str, object]:
    """Return current Flow profile status."""
    store, _settings = context()
    profile = store.get_flow_profile(model_key)
    if profile is None:
        return {"model_key": model_key, "status": "not_built", "region_count": 0}
    region_count = store.count_flow_regions(profile.id)
    return {
        "model_key": model_key,
        "status": profile.status,
        "region_count": region_count,
        "last_built_at": profile.last_built_at,
    }


@router.post("/feedback")
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
            request.seed_track_id, request.result_track_id,
            request.model, request.rating, request.note,
        )
    except ValueError as exc:
        logger.warning(
            "Feedback validation failed seed_track_id=%s result_track_id=%s model=%s error=%s",
            request.seed_track_id, request.result_track_id, request.model, exc,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(
        "Saved feedback seed_track_id=%s result_track_id=%s model=%s rating=%s",
        request.seed_track_id, request.result_track_id, request.model, request.rating,
    )
    return {"status": "ok"}
