"""Background analysis job implementations.

Extracted from app/main.py — Stage 6e.
Includes: embedding jobs, head-pack jobs, audio-feature jobs,
Navidrome sync, index build, and the auto-index scheduler.
"""
from __future__ import annotations

import logging
import multiprocessing
import os
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from time import perf_counter

from app.analysis_helpers import (
    analyze_progress,
    download_failure_hint,
    exception_detail,
    exception_traceback,
)
from app.audio_features import AUDIO_FEATURE_EXTRACTOR, AudioFeatureAnalyzer
from app.audio_source import has_navidrome_audio_source, track_audio_path
from app.config import MUQ_MULAN_MODEL
from app.embedder import DiscogsEffnetEmbedder, create_track_embedder
from app.head_pack import (
    DISCOGS_EFFNET_HEADS,
    DiscogsEffnetHeadPackAnalyzer,
    HeadOutput,
    download_model_file,
    required_model_files,
)
from app.logging_config import get_analysis_logger
from app.navidrome import NavidromeClient
from app.navidrome_sync import sync_navidrome_catalog
from app.recommender import build_index
from app.services.jobs import (
    create_deferred_job_if_busy,
    create_job,
    finish_job,
    maybe_start_next_deferred_job,
    update_job,
)
from app.state import (
    ACTIVE_JOB_STATUSES,
    ANALYZE_EXECUTORS,
    ANALYZE_EXECUTORS_LOCK,
    AUTO_INDEX_ANALYSIS_JOBS,
    AUTO_INDEX_LOCK,
    DEFAULT_ANALYZE_TF_THREADS,
    DEFAULT_ANALYZE_WORKERS,
    MAX_AUDIO_FEATURE_WORKERS,
    SHUTDOWN_REQUESTED,
)

logger = logging.getLogger(__name__)
analysis_logger = get_analysis_logger()


# ---------------------------------------------------------------------------
# Failure field builder (used by both local and subprocess workers)
# ---------------------------------------------------------------------------

def analyze_failure_fields(exc: Exception, stage: str) -> dict[str, str]:
    return {
        "error": str(exc),
        "error_type": type(exc).__name__,
        "traceback": traceback.format_exc(),
        "stage": stage,
    }


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AnalyzeResult:
    task_id: str | None
    track_id: int
    path: str
    status: str
    vector: object = None  # np.ndarray | None
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


# ---------------------------------------------------------------------------
# Failure helpers
# ---------------------------------------------------------------------------

def embedding_failure_stage(exc: Exception) -> str:
    message = str(exc).lower()
    if any(token in message for token in ["ffmpeg", "audio", "codec", "sample", "decode"]):
        return "load_audio"
    return "predict"


def mark_missing_after_failure(store, result) -> None:
    if result.error_type == "FileNotFoundError":
        store.mark_track_missing(result.track_id)


def analyze_failure_retryable(result) -> bool:
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


# ---------------------------------------------------------------------------
# Embedder / runtime setup
# ---------------------------------------------------------------------------

def create_analyze_embedder(settings, model: str) -> object:
    if model == MUQ_MULAN_MODEL:
        return create_track_embedder(settings, model)
    return DiscogsEffnetEmbedder(settings, model)


_WORKER_EMBEDDER: object | None = None
_WORKER_AUDIO_FEATURE_ANALYZER: AudioFeatureAnalyzer | None = None


def configure_analyze_runtime(tf_threads: int) -> None:
    os.environ["TF_NUM_INTRAOP_THREADS"] = str(tf_threads)
    os.environ["TF_NUM_INTEROP_THREADS"] = "1"
    os.environ["OMP_NUM_THREADS"] = str(tf_threads)


# ---------------------------------------------------------------------------
# Process pool worker init / extract functions (run in subprocesses)
# ---------------------------------------------------------------------------

def _init_embedding_worker(settings, model: str, tf_threads: int) -> None:
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
        return AudioFeaturesResult(track_id=track_id, path=path, status="ok", features=features)
    except Exception as exc:
        return AudioFeaturesResult(
            track_id=track_id,
            path=path,
            status="failed",
            **analyze_failure_fields(exc, "audio_features"),
        )


# ---------------------------------------------------------------------------
# Audio path helpers (used in local extraction)
# ---------------------------------------------------------------------------

def _prepare_analyze_audio_path(store, settings, track):
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
                **analyze_failure_fields(
                    exc,
                    "navidrome-download" if has_navidrome_audio_source(store, track) else embedding_failure_stage(exc),
                ),
            ),
        )


def _cleanup_audio_manager(manager: object | None) -> None:
    if manager is None:
        return
    try:
        manager.__exit__(None, None, None)
    except Exception:
        logger.debug("Audio source cleanup failed", exc_info=True)


# ---------------------------------------------------------------------------
# Process pool management
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Local extraction helpers
# ---------------------------------------------------------------------------

def _extract_embedding_local(embedder, store, settings, track) -> AnalyzeResult:
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


def _extract_heads_local(analyzer, store, settings, track) -> HeadAnalyzeResult:
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
        return HeadAnalyzeResult(track_id=track.id, path=track.path, status="ok", outputs=outputs)
    except Exception as exc:
        return HeadAnalyzeResult(
            track_id=track.id,
            path=track.path,
            status="failed",
            **analyze_failure_fields(exc, "analyze_heads"),
        )
    finally:
        _cleanup_audio_manager(manager)


def _extract_audio_features_local(analyzer, store, settings, track) -> AudioFeaturesResult:
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
        return AudioFeaturesResult(track_id=track.id, path=track.path, status="ok", features=features)
    except Exception as exc:
        return AudioFeaturesResult(
            track_id=track.id,
            path=track.path,
            status="failed",
            **analyze_failure_fields(exc, "audio_features"),
        )
    finally:
        _cleanup_audio_manager(manager)


# ---------------------------------------------------------------------------
# Track ↔ task conversion
# ---------------------------------------------------------------------------

def task_to_track(task):
    from app.models import Track
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


# ---------------------------------------------------------------------------
# Result iteration generators
# ---------------------------------------------------------------------------

def _iter_analyze_results(tracks, store, settings, model: str, workers: int, tf_threads: int):
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


def _iter_analyze_task_results(tasks, store, settings, model: str, workers: int, tf_threads: int):
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


def _iter_audio_feature_task_results(tasks, store, settings, workers: int):
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


# ---------------------------------------------------------------------------
# Auto-index scheduler
# ---------------------------------------------------------------------------

def schedule_auto_index_for_analysis(
    store,
    analysis_job_id: str,
    background_tasks=None,
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


# ---------------------------------------------------------------------------
# Background job implementations
# ---------------------------------------------------------------------------

def _check_missing_files_job(job_id: str) -> None:
    from app.api.deps import context
    try:
        store, _settings = context()
        total = store.count_tracks()
        logger.info("Starting missing-file check job_id=%s total=%s", job_id, total)
        update_job(job_id, status="running", total=total, message=f"Checking availability for {total} tracks")
        checked, missing = store.check_file_availability()
        update_job(job_id, done=checked, failed=0)
        finish_job(job_id, "completed", f"Checked {checked} tracks, lost files {missing}")
        logger.info("Finished missing-file check job_id=%s checked=%s missing=%s", job_id, checked, missing)
    except Exception as exc:
        logger.exception("Missing-file check failed job_id=%s", job_id)
        finish_job(job_id, "failed", str(exc))


def _navidrome_sync_job(job_id: str, page_size: int, limit: int | None, mark_stale: bool) -> None:
    from app.api.deps import context
    store = None
    try:
        store, settings = context()
        client = NavidromeClient(settings.navidrome)
        logger.info(
            "Starting Navidrome sync job job_id=%s page_size=%s limit=%s mark_stale=%s",
            job_id, page_size, limit, mark_stale,
        )
        known_total = limit or store.count_external_tracks("navidrome") or 0
        store.update_progress_job(job_id, status="running", total=known_total, message="Syncing Navidrome catalog")

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

        result = sync_navidrome_catalog(store, client, page_size=page_size, limit=limit, mark_stale=mark_stale, progress=progress)
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
    from app.api.deps import context
    try:
        if SHUTDOWN_REQUESTED:
            finish_job(job_id, "failed", "Analyze cancelled during application shutdown")
            return
        store, settings = context()
        if enqueue:
            durable_job = store.create_analysis_job(
                model, limit, kind="analyze",
                local_executor_enabled=local_executor_enabled,
                workers=workers, tf_threads=tf_threads,
                max_attempts=max_attempts, job_id=job_id,
            )
        else:
            durable_job = store.get_analysis_job(job_id)
            if durable_job is None:
                raise ValueError(f"Analysis job not found: {job_id}")
        total = durable_job.total
        started_at = perf_counter()
        analysis_logger.info(
            "Starting analyze job job_id=%s model=%s limit=%s workers=%s tf_threads=%s local_executor=%s total=%s",
            job_id, model, limit, workers, tf_threads, local_executor_enabled, total,
        )
        update_job(
            job_id, status="running", total=total,
            message=f"Analyzing {total} tracks with {model} on {workers} worker(s), tf_threads={tf_threads}",
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
            tasks = store.claim_analysis_tasks(local_worker_id, [model], limit=max(workers, 1), lease_seconds=3600)
            if not tasks:
                durable_job = store.get_analysis_job(job_id)
                if durable_job is None or durable_job.status not in ACTIVE_JOB_STATUSES:
                    break
                update_job(
                    job_id, done=durable_job.done, failed=durable_job.failed,
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
                            result.task_id, error=str(exc), error_type=type(exc).__name__,
                            stage="save", worker_id=local_worker_id, retryable=False,
                        )
                        analysis_logger.exception(
                            "Analyze save failed job_id=%s track_id=%s path=%s model=%s stage=save",
                            job_id, result.track_id, result.path, model,
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
                        job_id, result.track_id, result.path, model,
                        result.stage, result.error_type, result.error, result.traceback or "",
                    )
                durable_job = store.get_analysis_job(job_id)
                if durable_job is not None:
                    done = durable_job.done
                    failed = durable_job.failed
                    update_job(
                        job_id, done=done, failed=failed, current=result.path,
                        message=durable_job.message,
                        **analyze_progress(started_at, total, done, failed),
                    )
                    if (done + failed) % 25 == 0 or done + failed == total:
                        progress = analyze_progress(started_at, total, done, failed)
                        analysis_logger.info(
                            "Analyze progress job_id=%s model=%s done=%s failed=%s total=%s elapsed=%.1f tracks_per_min=%s eta_seconds=%s",
                            job_id, model, done, failed, total,
                            progress["elapsed_seconds"], progress["tracks_per_min"], progress["eta_seconds"],
                        )
        durable_job = store.get_analysis_job(job_id)
        if durable_job is not None:
            done = durable_job.done
            failed = durable_job.failed
        analysis_logger.info(
            "Finished analyze job job_id=%s model=%s done=%s failed=%s total=%s",
            job_id, model, done, failed, total,
        )
        finish_job(job_id, "completed", f"Analyzed {done} tracks, failed {failed}")
        schedule_auto_index_for_analysis(store, job_id)
    except Exception as exc:
        analysis_logger.exception("Analyze job failed job_id=%s model=%s", job_id, model)
        finish_job(job_id, "failed", str(exc))


def _analyze_heads_job(
    job_id: str,
    limit: int | None,
    local_executor_enabled: bool = True,
    max_attempts: int = 3,
    enqueue: bool = True,
) -> None:
    from app.api.deps import context
    try:
        if SHUTDOWN_REQUESTED:
            finish_job(job_id, "failed", "Head analysis cancelled during application shutdown")
            return
        store, settings = context()
        head_model_names = [head.id for head in DISCOGS_EFFNET_HEADS]
        if enqueue:
            tracks = store.list_tracks_missing_head_pack(head_model_names, limit=limit)
            durable_job = store.create_analysis_job(
                "discogs-effnet-heads", limit, kind="analyze-heads",
                tracks=tracks, local_executor_enabled=local_executor_enabled,
                max_attempts=max_attempts, job_id=job_id,
            )
        else:
            durable_job = store.get_analysis_job(job_id)
            if durable_job is None:
                raise ValueError(f"Head analysis job not found: {job_id}")
        total = durable_job.total
        started_at = perf_counter()
        analysis_logger.info(
            "Starting analyze-heads job job_id=%s limit=%s total=%s heads=%s",
            job_id, limit, total, len(head_model_names),
        )
        update_job(
            job_id, status="running", total=total,
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
            tasks = store.claim_analysis_tasks(local_worker_id, ["discogs-effnet-heads"], limit=1, lease_seconds=3600)
            if not tasks:
                durable_job = store.get_analysis_job(job_id)
                if durable_job is None or durable_job.status not in ACTIVE_JOB_STATUSES:
                    break
                update_job(
                    job_id, done=durable_job.done, failed=durable_job.failed,
                    message=durable_job.message,
                    **analyze_progress(started_at, total, durable_job.done, durable_job.failed),
                )
                time.sleep(2)
                continue
            task = tasks[0]
            track = store.get_track(task.track_id)
            if track is None:
                store.fail_analysis_task(
                    task.id, error="Track not found", error_type="ValueError",
                    stage="load_track", worker_id=local_worker_id, retryable=False,
                )
                continue
            update_job(job_id, current=track.path, message=f"Analyzing heads for {track.path}")
            result = _extract_heads_local(analyzer, store, settings, track)
            if result.status == "ok" and result.outputs is not None:
                try:
                    for output in result.outputs:
                        store.save_model_output(result.track_id, output.model_name, output.scores, output.aggregation)
                        store.save_predictions(result.track_id, output.model_name, output.predictions)
                    store.mark_track_available(result.track_id)
                    store.complete_analysis_task(task.id, local_worker_id)
                except Exception as exc:
                    store.fail_analysis_task(
                        task.id, error=str(exc), error_type=type(exc).__name__,
                        stage="save", worker_id=local_worker_id, retryable=False,
                    )
                    analysis_logger.exception(
                        "Head analysis save failed job_id=%s track_id=%s path=%s stage=save",
                        job_id, result.track_id, result.path,
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
                    job_id, result.track_id, result.path,
                    result.stage, result.error_type, result.error, result.traceback or "",
                )
            durable_job = store.get_analysis_job(job_id)
            if durable_job is None:
                continue
            done = durable_job.done
            failed = durable_job.failed
            update_job(
                job_id, done=done, failed=failed, current=result.path,
                message=durable_job.message,
                **analyze_progress(started_at, total, done, failed),
            )
            if (done + failed) % 25 == 0 or done + failed == total:
                progress = analyze_progress(started_at, total, done, failed)
                analysis_logger.info(
                    "Analyze-heads progress job_id=%s done=%s failed=%s total=%s elapsed=%.1f tracks_per_min=%s eta_seconds=%s",
                    job_id, done, failed, total,
                    progress["elapsed_seconds"], progress["tracks_per_min"], progress["eta_seconds"],
                )
        analysis_logger.info(
            "Finished analyze-heads job job_id=%s done=%s failed=%s total=%s",
            job_id, done, failed, total,
        )
        finish_job(job_id, "completed", f"Analyzed heads for {done} tracks, failed {failed}")
    except Exception as exc:
        analysis_logger.exception("Analyze-heads job failed job_id=%s", job_id)
        finish_job(job_id, "failed", str(exc))


def _analyze_genres_job(job_id: str, _model: str, limit: int | None) -> None:
    _analyze_heads_job(job_id, limit)


def _download_head_models_job(job_id: str) -> None:
    from app.api.deps import context
    try:
        _store, settings = context()
        files = required_model_files()
        total = len(files)
        logger.info("Starting download head models job job_id=%s files=%s", job_id, total)
        update_job(job_id, status="running", total=total, message="Downloading head models")
        downloaded = 0
        ready = 0
        for filename, source_url in files:
            update_job(job_id, current=filename, message=f"Checking {filename}")
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
                    job_id, "failed",
                    f"Failed to download {filename}: {hint or detail.splitlines()[0]}",
                    error_detail="\n".join(lines),
                )
                return
            if result.downloaded:
                downloaded += 1
            ready += 1
            update_job(job_id, done=ready, current=str(result.path), message=f"Ready {ready}/{total} head model files, downloaded {downloaded}")
        finish_job(job_id, "completed", f"Head model files ready: {ready}/{total}, downloaded {downloaded}")
    except Exception as exc:
        logger.exception("Download head models job failed job_id=%s", job_id)
        detail = exception_detail(exc)
        finish_job(job_id, "failed", detail.splitlines()[0], error_detail=exception_traceback(exc))


def _analyze_audio_features_job(
    job_id: str,
    limit: int | None,
    workers: int = 1,
    local_executor_enabled: bool = True,
    max_attempts: int = 3,
    enqueue: bool = True,
) -> None:
    from app.api.deps import context
    try:
        if SHUTDOWN_REQUESTED:
            finish_job(job_id, "failed", "Audio feature analysis cancelled during application shutdown")
            return
        store, settings = context()
        if enqueue:
            tracks = store.list_tracks_missing_features(AUDIO_FEATURE_EXTRACTOR, limit=limit)
            durable_job = store.create_analysis_job(
                AUDIO_FEATURE_EXTRACTOR, limit, kind="analyze-audio-features",
                tracks=tracks, local_executor_enabled=local_executor_enabled,
                max_attempts=max_attempts, job_id=job_id,
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
            job_id, limit, total, AUDIO_FEATURE_EXTRACTOR, workers,
        )
        update_job(
            job_id, status="running", total=total,
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
                local_worker_id, [AUDIO_FEATURE_EXTRACTOR],
                limit=local_claim_batch_size, lease_seconds=3600,
            )
            if not tasks:
                durable_job = store.get_analysis_job(job_id)
                if durable_job is None or durable_job.status not in ACTIVE_JOB_STATUSES:
                    break
                update_job(
                    job_id, done=durable_job.done, failed=durable_job.failed,
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
                            task.id, error=str(exc), error_type=type(exc).__name__,
                            stage="save", worker_id=local_worker_id, retryable=False,
                        )
                        analysis_logger.exception(
                            "Audio feature save failed job_id=%s track_id=%s path=%s extractor=%s stage=save",
                            job_id, result.track_id, result.path, AUDIO_FEATURE_EXTRACTOR,
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
                        job_id, result.track_id, result.path, AUDIO_FEATURE_EXTRACTOR,
                        result.stage, result.error_type, result.error, result.traceback or "",
                    )
                durable_job = store.get_analysis_job(job_id)
                if durable_job is None:
                    continue
                done = durable_job.done
                failed = durable_job.failed
                update_job(
                    job_id, done=done, failed=failed, current=result.path,
                    message=durable_job.message,
                    **analyze_progress(started_at, total, done, failed),
                )
                if (done + failed) % 25 == 0 or done + failed == total:
                    progress = analyze_progress(started_at, total, done, failed)
                    analysis_logger.info(
                        "Analyze-audio-features progress job_id=%s done=%s failed=%s total=%s elapsed=%.1f tracks_per_min=%s eta_seconds=%s",
                        job_id, done, failed, total,
                        progress["elapsed_seconds"], progress["tracks_per_min"], progress["eta_seconds"],
                    )
        analysis_logger.info(
            "Finished analyze-audio-features job job_id=%s done=%s failed=%s total=%s",
            job_id, done, failed, total,
        )
        finish_job(job_id, "completed", f"Analyzed audio features for {done} tracks, failed {failed}")
    except Exception as exc:
        analysis_logger.exception("Analyze-audio-features job failed job_id=%s", job_id)
        finish_job(job_id, "failed", str(exc))


def _index_job(job_id: str, model: str) -> None:
    from app.api.deps import context
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
