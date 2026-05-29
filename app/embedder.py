from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

import numpy as np

from app.config import MODEL_OUTPUTS, Settings


FFMPEG_FIRST_EXTENSIONS = {".opus"}
DEFAULT_AUDIO_LOADER = "ffmpeg"
logger = logging.getLogger(__name__)


class FfmpegDecodeError(RuntimeError):
    pass


class DiscogsEffnetEmbedder:
    def __init__(self, settings: Settings, model_name: str):
        self.settings = settings
        self.model_name = model_name
        self.model_path = settings.model_path(model_name)
        self.output = MODEL_OUTPUTS.get(model_name, "PartitionedCall:1")
        self._model = None

    def extract_track_vector(self, path: Path) -> np.ndarray:
        logger.info(
            "Extracting track vector path=%s model=%s model_path=%s",
            path,
            self.model_name,
            self.model_path,
        )
        embeddings = self.extract_patch_embeddings(path)
        return pool_and_normalize(embeddings)

    def extract_patch_embeddings(self, path: Path) -> np.ndarray:
        audio = self._load_audio(path)
        return self._predict(audio)

    def _load_audio(self, path: Path) -> np.ndarray:
        configure_tensorflow_logging()
        loader = os.getenv("DISCOCS_AUDIO_LOADER", DEFAULT_AUDIO_LOADER).lower()
        if loader not in {"ffmpeg", "essentia"}:
            logger.error("Invalid audio loader loader=%s path=%s", loader, path)
            raise ValueError("DISCOCS_AUDIO_LOADER must be 'ffmpeg' or 'essentia'")
        logger.debug("Loading audio path=%s loader=%s", path, loader)
        if loader == "ffmpeg" or path.suffix.lower() in FFMPEG_FIRST_EXTENSIONS:
            return load_audio_with_ffmpeg(path)
        return load_audio_with_essentia(path)

    def _predict(self, audio: np.ndarray) -> np.ndarray:
        configure_tensorflow_logging()
        if not self.model_path.exists():
            logger.error("Model file not found model=%s path=%s", self.model_name, self.model_path)
            raise FileNotFoundError(f"Model file not found: {self.model_path}")
        if self._model is None:
            try:
                from essentia.standard import TensorflowPredictEffnetDiscogs
            except ImportError as exc:
                logger.error("Essentia missing for embedding inference model=%s", self.model_name)
                raise RuntimeError(
                    "essentia-tensorflow is required for Discogs-EffNet inference"
                ) from exc
            logger.info("Loading embedding model model=%s path=%s", self.model_name, self.model_path)
            self._model = TensorflowPredictEffnetDiscogs(
                graphFilename=str(self.model_path),
                output=self.output,
            )
        return np.asarray(self._model(audio), dtype=np.float32)


def load_audio_with_essentia(path: Path) -> np.ndarray:
    try:
        from essentia.standard import MonoLoader
    except ImportError as exc:
        logger.error("Essentia missing for audio loading path=%s", path)
        raise RuntimeError(
            "essentia-tensorflow is required for audio embedding extraction"
        ) from exc
    try:
        return MonoLoader(filename=str(path), sampleRate=16000, resampleQuality=4)()
    except RuntimeError as exc:
        if "Unsupported codec" not in str(exc):
            raise
        logger.warning("Essentia unsupported codec, falling back to ffmpeg path=%s", path)
        return load_audio_with_ffmpeg(path)


def configure_tensorflow_logging() -> None:
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")


def load_audio_with_ffmpeg(path: Path) -> np.ndarray:
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")
    ffmpeg_threads = os.getenv("DISCOCS_FFMPEG_THREADS")
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    if ffmpeg_threads:
        command.extend(["-threads", ffmpeg_threads])
    command.extend([
        "-i",
        str(path),
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-",
    ])
    try:
        completed = subprocess.run(command, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        stdout = exc.stdout.decode("utf-8", errors="replace") if exc.stdout else ""
        details = stderr.strip() or stdout.strip() or "ffmpeg produced no diagnostic output"
        message = (
            f"ffmpeg failed to decode {path} with exit code {exc.returncode}: {details}"
        )
        logger.error(
            "ffmpeg failed path=%s returncode=%s stderr=%s",
            path,
            exc.returncode,
            details,
        )
        raise FfmpegDecodeError(message) from exc
    audio = np.frombuffer(completed.stdout, dtype=np.float32)
    if audio.size == 0:
        logger.error("ffmpeg decoded no audio samples path=%s", path)
        raise RuntimeError(f"ffmpeg decoded no audio samples from {path}")
    return audio


def pool_and_normalize(embeddings: np.ndarray) -> np.ndarray:
    if embeddings.ndim == 1:
        pooled = embeddings.astype(np.float32)
    elif embeddings.ndim == 2:
        pooled = embeddings.mean(axis=0).astype(np.float32)
    else:
        raise ValueError(f"Expected 1D or 2D embeddings, got shape {embeddings.shape}")

    norm = np.linalg.norm(pooled)
    if norm == 0:
        raise ValueError("Embedding vector has zero norm")
    return (pooled / norm).astype(np.float32)
