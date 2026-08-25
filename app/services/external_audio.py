"""Query vectors for audio that is not part of the catalog.

An external seed — a link the bot downloaded, a file someone sent to the bot —
is turned into a query vector and thrown away. This module is read-only by
contract: it never writes embeddings, tracks, model outputs, or index entries,
so external listening cannot drift the catalog or the HNSW index. The only
state it keeps is an in-process LRU of recent query vectors, which lives in
memory and dies with the process. See docs/external-audio.md.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import numpy as np

from app.config import Settings
from app.embedder import FfmpegDecodeError, create_track_embedder

logger = logging.getLogger(__name__)

# Длинный DJ-сет не имеет одного «звучания» целиком, а полный прогон модели по
# двухчасовому миксу стоит минуты CPU. Берём окно из середины: начало и конец у
# миксов — интро/аутро, наименее характерная часть.
ANALYSIS_WINDOW_SECONDS = 600.0
LONG_AUDIO_THRESHOLD_SECONDS = 720.0

VECTOR_CACHE_SIZE = 64
FFPROBE_TIMEOUT_SECONDS = 30
FFMPEG_TRIM_TIMEOUT_SECONDS = 600
DEFAULT_ANALYSIS_CONCURRENCY = 2
ANALYSIS_SLOT_TIMEOUT_SECONDS = 300.0

_HASH_CHUNK_BYTES = 1024 * 1024


class ExternalAudioError(RuntimeError):
    """The submitted bytes are not audio we can decode."""


class ExternalAudioBusy(RuntimeError):
    """All analysis slots are taken."""


@dataclass(frozen=True, slots=True)
class AudioProbe:
    has_audio_stream: bool
    duration_seconds: float | None


@dataclass(frozen=True, slots=True)
class ExternalVector:
    vector: np.ndarray
    duration_seconds: float | None
    analyzed_seconds: float | None
    analysis_offset_seconds: float
    cached: bool


_CACHE_LOCK = threading.Lock()
_VECTOR_CACHE: "OrderedDict[tuple[str, str], np.ndarray]" = OrderedDict()


def _analysis_concurrency() -> int:
    raw = os.getenv("DISCOCS_EXTERNAL_ANALYSIS_CONCURRENCY")
    try:
        value = int(raw) if raw else DEFAULT_ANALYSIS_CONCURRENCY
    except ValueError:
        value = DEFAULT_ANALYSIS_CONCURRENCY
    return max(1, value)


_ANALYSIS_SLOTS = threading.BoundedSemaphore(_analysis_concurrency())


def reset_vector_cache() -> None:
    with _CACHE_LOCK:
        _VECTOR_CACHE.clear()


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_HASH_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def probe_audio(path: Path) -> AudioProbe | None:
    """Duration and audio-stream presence, or None when ffprobe is unusable.

    A missing ffprobe is not fatal: without a probe we simply analyze the whole
    file and let the decoder report a broken input.
    """
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_type",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            timeout=FFPROBE_TIMEOUT_SECONDS,
        )
        payload = json.loads(completed.stdout.decode("utf-8", errors="replace") or "{}")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        logger.debug("ffprobe unavailable or failed path=%s error=%s", path, exc)
        return None

    streams = payload.get("streams") or []
    raw_duration = (payload.get("format") or {}).get("duration")
    duration: float | None
    try:
        duration = float(raw_duration) if raw_duration is not None else None
    except (TypeError, ValueError):
        duration = None
    if duration is not None and duration <= 0:
        duration = None
    return AudioProbe(has_audio_stream=bool(streams), duration_seconds=duration)


def analysis_window(duration_seconds: float | None) -> tuple[float, float] | None:
    """Middle window to analyze, or None to analyze the whole file."""
    if duration_seconds is None or duration_seconds <= LONG_AUDIO_THRESHOLD_SECONDS:
        return None
    start = (duration_seconds - ANALYSIS_WINDOW_SECONDS) / 2
    return max(0.0, start), ANALYSIS_WINDOW_SECONDS


def trim_window(path: Path, start_seconds: float, seconds: float, work_dir: Path) -> Path:
    """Cut a lossless window so the embedder sees the same audio it would decode.

    FLAC keeps the source sample rate, so any model's own resampling stays
    unchanged; the models differ in what rate they want (16k EffNet, 24k MuQ).
    """
    out_path = work_dir / f"{uuid4().hex}-window.flac"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start_seconds:.3f}",
        "-t",
        f"{seconds:.3f}",
        "-i",
        str(path),
        "-vn",
        "-c:a",
        "flac",
        "-compression_level",
        "0",
        str(out_path),
    ]
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            timeout=FFMPEG_TRIM_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        out_path.unlink(missing_ok=True)
        logger.warning("Could not trim analysis window path=%s error=%s", path, exc)
        raise ExternalAudioError("Could not extract an analysis window from the audio") from exc
    return out_path


def _cached_vector(key: tuple[str, str]) -> np.ndarray | None:
    with _CACHE_LOCK:
        vector = _VECTOR_CACHE.get(key)
        if vector is None:
            return None
        _VECTOR_CACHE.move_to_end(key)
        return vector


def _remember_vector(key: tuple[str, str], vector: np.ndarray) -> None:
    with _CACHE_LOCK:
        _VECTOR_CACHE[key] = vector
        _VECTOR_CACHE.move_to_end(key)
        while len(_VECTOR_CACHE) > VECTOR_CACHE_SIZE:
            _VECTOR_CACHE.popitem(last=False)


def extract_query_vector(
    settings: Settings,
    model_name: str,
    path: Path,
    work_dir: Path,
) -> ExternalVector:
    """Embed external audio into a query vector. Nothing is persisted."""
    digest = file_digest(path)
    probe = probe_audio(path)
    if probe is not None and not probe.has_audio_stream:
        raise ExternalAudioError("The submitted file has no audio stream")

    duration = probe.duration_seconds if probe else None
    window = analysis_window(duration)
    cache_key = (digest, model_name)
    cached = _cached_vector(cache_key)
    if cached is not None:
        logger.info(
            "External query vector cache hit model=%s digest=%s duration=%s",
            model_name,
            digest[:12],
            duration,
        )
        return ExternalVector(
            vector=cached,
            duration_seconds=duration,
            analyzed_seconds=window[1] if window else duration,
            analysis_offset_seconds=window[0] if window else 0.0,
            cached=True,
        )

    if not _ANALYSIS_SLOTS.acquire(timeout=ANALYSIS_SLOT_TIMEOUT_SECONDS):
        raise ExternalAudioBusy("External audio analysis is busy")
    window_path: Path | None = None
    started = perf_counter()
    try:
        source = path
        if window is not None:
            window_path = trim_window(path, window[0], window[1], work_dir)
            source = window_path
        embedder = create_track_embedder(settings, model_name)
        try:
            vector = embedder.extract_track_vector(source)
        except FfmpegDecodeError as exc:
            raise ExternalAudioError("Could not decode the submitted audio") from exc
        except RuntimeError as exc:
            if "no audio samples" in str(exc):
                raise ExternalAudioError("The submitted audio decoded to silence") from exc
            raise
    finally:
        if window_path is not None:
            window_path.unlink(missing_ok=True)
        _ANALYSIS_SLOTS.release()

    vector = np.asarray(vector, dtype=np.float32).reshape(-1)
    _remember_vector(cache_key, vector)
    logger.info(
        "External query vector extracted model=%s digest=%s duration=%s window=%s seconds=%.3f",
        model_name,
        digest[:12],
        duration,
        window,
        perf_counter() - started,
    )
    return ExternalVector(
        vector=vector,
        duration_seconds=duration,
        analyzed_seconds=window[1] if window else duration,
        analysis_offset_seconds=window[0] if window else 0.0,
        cached=False,
    )
