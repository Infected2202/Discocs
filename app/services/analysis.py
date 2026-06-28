"""Analysis pipeline: data classes, worker-pool management, extraction helpers,
and result iterators.

Extracted from app/analysis_jobs.py — Stage 5e.
The *_job orchestration functions remain in app/analysis_jobs.py.
"""
from __future__ import annotations

import logging
import multiprocessing
import os
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path

from app.analysis_helpers import (
    exception_detail,
    exception_traceback,
)
from app.audio_features import AUDIO_FEATURE_EXTRACTOR, AudioFeatureAnalyzer
from app.audio_source import has_navidrome_audio_source, track_audio_path
from app.config import MUQ_MULAN_MODEL
from app.embedder import DiscogsEffnetEmbedder, create_track_embedder
from app.head_pack import DISCOGS_EFFNET_HEADS, DiscogsEffnetHeadPackAnalyzer, HeadOutput
from app.logging_config import get_analysis_logger
from app.state import (
    ANALYZE_EXECUTORS,
    ANALYZE_EXECUTORS_LOCK,
    SHUTDOWN_REQUESTED,
)

logger = logging.getLogger(__name__)
analysis_logger = get_analysis_logger()


# ---------------------------------------------------------------------------
# Failure field builder
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


def configure_analyze_runtime(tf_threads: int) -> None:
    os.environ["TF_NUM_INTRAOP_THREADS"] = str(tf_threads)
    os.environ["TF_NUM_INTEROP_THREADS"] = "1"
    os.environ["OMP_NUM_THREADS"] = str(tf_threads)


# ---------------------------------------------------------------------------
# Process pool worker init / extract (run in subprocesses)
# ---------------------------------------------------------------------------

_WORKER_EMBEDDER: object | None = None
_WORKER_AUDIO_FEATURE_ANALYZER: AudioFeatureAnalyzer | None = None


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
# Audio path helpers
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
    import app.state as _state
    if _state.SHUTDOWN_REQUESTED:
        return
    configure_analyze_runtime(tf_threads)
    if workers <= 1 or model == MUQ_MULAN_MODEL:
        embedder = create_analyze_embedder(settings, model)
        for track in tracks:
            if _state.SHUTDOWN_REQUESTED:
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
            if _state.SHUTDOWN_REQUESTED:
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
        if _state.SHUTDOWN_REQUESTED:
            terminate_process_pool(executor)
        else:
            try:
                executor.shutdown(wait=True, cancel_futures=False)
            except Exception:
                pass


def _iter_analyze_task_results(tasks, store, settings, model: str, workers: int, tf_threads: int):
    import app.state as _state
    if _state.SHUTDOWN_REQUESTED:
        return
    configure_analyze_runtime(tf_threads)
    if workers <= 1 or model == MUQ_MULAN_MODEL:
        embedder = create_analyze_embedder(settings, model)
        for task in tasks:
            if _state.SHUTDOWN_REQUESTED:
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
            if _state.SHUTDOWN_REQUESTED:
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
        if _state.SHUTDOWN_REQUESTED:
            terminate_process_pool(executor)
        else:
            try:
                executor.shutdown(wait=True, cancel_futures=False)
            except Exception:
                pass


def _iter_audio_feature_task_results(tasks, store, settings, workers: int):
    import app.state as _state
    if _state.SHUTDOWN_REQUESTED:
        return
    if workers <= 1:
        analyzer = AudioFeatureAnalyzer()
        for task in tasks:
            if _state.SHUTDOWN_REQUESTED:
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
            if _state.SHUTDOWN_REQUESTED:
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
        if _state.SHUTDOWN_REQUESTED:
            terminate_process_pool(executor)
        else:
            try:
                executor.shutdown(wait=True, cancel_futures=False)
            except Exception:
                pass
