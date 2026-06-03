from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

import numpy as np

from app.config import MODEL_OUTPUTS, Settings


FFMPEG_FIRST_EXTENSIONS = {".opus"}
DEFAULT_AUDIO_LOADER = "ffmpeg"
DEFAULT_EFFNET_BACKEND = "auto"
logger = logging.getLogger(__name__)


class FfmpegDecodeError(RuntimeError):
    pass


class DiscogsEffnetEmbedder:
    def __init__(
        self,
        settings: Settings,
        model_name: str,
        batch_size: int | None = None,
        backend: str | None = None,
    ):
        self.settings = settings
        self.model_name = model_name
        self.model_path = settings.model_path(model_name)
        self.output = MODEL_OUTPUTS.get(model_name, "PartitionedCall:1")
        self.batch_size = batch_size
        self.backend = (backend or os.getenv("DISCOCS_EFFNET_BACKEND", DEFAULT_EFFNET_BACKEND)).lower()
        self._model = None
        self._direct_model: DirectTensorflowEffnet | None = None

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

    def extract_direct_patches(self, path: Path) -> np.ndarray:
        audio = self._load_audio(path)
        return audio_to_effnet_patches(audio)

    def predict_direct_patches(self, patches: np.ndarray) -> np.ndarray:
        if self._direct_model is None:
            self._direct_model = DirectTensorflowEffnet(
                self.model_path,
                self.output,
                batch_size=self.resolved_batch_size(default=64),
            )
        return self._direct_model.predict_patches(patches)

    def direct_model(self) -> "DirectTensorflowEffnet":
        if self._direct_model is None:
            self._direct_model = DirectTensorflowEffnet(
                self.model_path,
                self.output,
                batch_size=self.resolved_batch_size(default=64),
            )
        return self._direct_model

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
        if self.backend not in {"auto", "essentia", "tensorflow"}:
            raise ValueError("DISCOCS_EFFNET_BACKEND must be 'auto', 'essentia', or 'tensorflow'")
        if self.backend in {"auto", "tensorflow"}:
            try:
                if self._direct_model is None:
                    self._direct_model = DirectTensorflowEffnet(
                        self.model_path,
                        self.output,
                        batch_size=self.resolved_batch_size(default=64),
                    )
                return self._direct_model.predict(audio)
            except Exception:
                if self.backend == "tensorflow":
                    raise
                logger.exception(
                    "Direct TensorFlow EffNet backend failed, falling back to Essentia backend model=%s",
                    self.model_name,
                )
        if self._model is None:
            try:
                from essentia.standard import TensorflowPredictEffnetDiscogs
            except ImportError as exc:
                logger.error("Essentia missing for embedding inference model=%s", self.model_name)
                raise RuntimeError(
                    "essentia-tensorflow is required for Discogs-EffNet inference"
                ) from exc
            logger.info("Loading embedding model model=%s path=%s", self.model_name, self.model_path)
            kwargs = {
                "graphFilename": str(self.model_path),
                "output": self.output,
            }
            batch_size = self.resolved_batch_size(default=None)
            if batch_size is not None:
                kwargs["batchSize"] = int(batch_size)
            self._model = TensorflowPredictEffnetDiscogs(**kwargs)
        return np.asarray(self._model(audio), dtype=np.float32)

    def resolved_batch_size(self, default: int | None) -> int | None:
        if self.batch_size is not None:
            return int(self.batch_size)
        raw_batch_size = os.getenv("DISCOCS_EFFNET_BATCH_SIZE")
        return int(raw_batch_size) if raw_batch_size else default


class DirectTensorflowEffnet:
    patch_size = 128
    patch_hop_size = 62
    mel_bands = 96

    def __init__(self, model_path: Path, output: str, batch_size: int = 64):
        self.model_path = model_path
        self.output_name = output
        self.batch_size = int(batch_size)
        self._mel_input = None
        self._session = None
        self._input_tensor = None
        self._output_tensor = None

    def predict(self, audio: np.ndarray) -> np.ndarray:
        patches = audio_to_effnet_patches(audio)
        return self.predict_patches(patches)

    def predict_patches(self, patches: np.ndarray) -> np.ndarray:
        if patches.size == 0:
            return np.empty((0, 1280), dtype=np.float32)
        self._ensure_session()
        outputs = []
        batch_size = self.batch_size
        for start in range(0, len(patches), batch_size):
            batch = patches[start : start + batch_size]
            actual = len(batch)
            if actual < batch_size:
                batch = np.pad(batch, ((0, batch_size - actual), (0, 0), (0, 0)), mode="constant")
            output = self._session.run(self._output_tensor, feed_dict={self._input_tensor: batch})
            outputs.append(np.asarray(output[:actual], dtype=np.float32))
        return np.concatenate(outputs, axis=0)

    def predict_patches_unpadded(self, patches: np.ndarray) -> np.ndarray:
        if patches.shape[0] != self.batch_size:
            raise ValueError(f"Expected exactly {self.batch_size} patches, got {patches.shape[0]}")
        self._ensure_session()
        return np.asarray(self._session.run(self._output_tensor, feed_dict={self._input_tensor: patches}), dtype=np.float32)

    def _ensure_session(self) -> None:
        if self._session is not None:
            return
        try:
            import tensorflow as tf
        except ImportError as exc:
            raise RuntimeError("tensorflow is required for direct EffNet inference") from exc
        graph_def = tf.compat.v1.GraphDef()
        graph_def.ParseFromString(self.model_path.read_bytes())
        graph = tf.Graph()
        with graph.as_default():
            tf.import_graph_def(graph_def, name="")
        self._input_tensor = graph.get_tensor_by_name("serving_default_melspectrogram:0")
        self._output_tensor = graph.get_tensor_by_name(self.output_name)
        config = tf.compat.v1.ConfigProto(allow_soft_placement=True)
        config.gpu_options.allow_growth = True
        self._session = tf.compat.v1.Session(graph=graph, config=config)
        logger.info(
            "Loaded direct TensorFlow EffNet backend model=%s output=%s batch_size=%s",
            self.model_path,
            self.output_name,
            self.batch_size,
        )


def audio_to_effnet_patches(audio: np.ndarray) -> np.ndarray:
    try:
        from essentia.standard import FrameGenerator, TensorflowInputMusiCNN
    except ImportError as exc:
        raise RuntimeError("essentia-tensorflow is required for EffNet mel preprocessing") from exc
    mel_input = TensorflowInputMusiCNN()
    frames = [
        np.asarray(mel_input(frame), dtype=np.float32)
        for frame in FrameGenerator(audio, frameSize=512, hopSize=256, startFromZero=True)
    ]
    if not frames:
        return np.empty((0, DirectTensorflowEffnet.patch_size, DirectTensorflowEffnet.mel_bands), dtype=np.float32)
    mels = np.asarray(frames, dtype=np.float32)
    patches = [
        mels[start : start + DirectTensorflowEffnet.patch_size]
        for start in range(
            0,
            max(mels.shape[0] - DirectTensorflowEffnet.patch_size + 1, 0),
            DirectTensorflowEffnet.patch_hop_size,
        )
    ]
    return np.asarray(patches, dtype=np.float32)


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
