from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Protocol

import numpy as np

from app.config import MODEL_OUTPUTS, MUQ_MULAN_MODEL, Settings


FFMPEG_FIRST_EXTENSIONS = {".opus"}
DEFAULT_AUDIO_LOADER = "ffmpeg"
DEFAULT_EFFNET_BACKEND = "auto"
MUQ_MULAN_SAMPLE_RATE = 24000
DEFAULT_MUQ_MULAN_MODEL_NAME = "OpenMuQ/MuQ-MuLan-large"
DEFAULT_MUQ_MULAN_DEVICE = "auto"
MUQ_MULAN_REQUIRED_REPOS = (
    DEFAULT_MUQ_MULAN_MODEL_NAME,
    "OpenMuQ/MuQ-large-msd-iter",
    "FacebookAI/xlm-roberta-base",
)
logger = logging.getLogger(__name__)


class FfmpegDecodeError(RuntimeError):
    pass


class TrackEmbedder(Protocol):
    model_name: str

    def extract_track_vector(self, path: Path) -> np.ndarray:
        ...


def _ensure_int_max_str_digits_compat(sys_module) -> None:
    if not hasattr(sys_module, "get_int_max_str_digits"):
        def get_int_max_str_digits() -> int:
            return 0

        sys_module.get_int_max_str_digits = get_int_max_str_digits
    if not hasattr(sys_module, "set_int_max_str_digits"):
        def set_int_max_str_digits(maxdigits: int) -> None:
            return None

        sys_module.set_int_max_str_digits = set_int_max_str_digits


def create_track_embedder(
    settings: Settings,
    model_name: str,
    *,
    batch_size: int | None = None,
    backend: str | None = None,
) -> TrackEmbedder:
    if model_name == MUQ_MULAN_MODEL:
        return MuqMulanEmbedder(settings, model_name=model_name)
    return DiscogsEffnetEmbedder(settings, model_name, batch_size=batch_size, backend=backend)


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


class MuqMulanEmbedder:
    sample_rate = MUQ_MULAN_SAMPLE_RATE

    def __init__(
        self,
        settings: Settings,
        model_name: str = MUQ_MULAN_MODEL,
        *,
        pretrained_name: str | None = None,
        cache_dir: Path | None = None,
        device: str | None = None,
    ):
        if model_name != MUQ_MULAN_MODEL:
            raise ValueError(f"MuqMulanEmbedder only supports model_name={MUQ_MULAN_MODEL!r}")
        self.settings = settings
        self.model_name = model_name
        self.pretrained_name = pretrained_name or os.getenv(
            "DISCOCS_MUQ_MODEL_NAME",
            DEFAULT_MUQ_MULAN_MODEL_NAME,
        )
        raw_cache_dir = cache_dir or os.getenv("DISCOCS_MUQ_CACHE_DIR")
        self.cache_dir = Path(raw_cache_dir) if raw_cache_dir else settings.model_dir / "muq"
        self.device = (device or os.getenv("DISCOCS_MUQ_DEVICE", DEFAULT_MUQ_MULAN_DEVICE)).lower()
        self._model = None
        self._torch = None
        self._resolved_device = None
        self._prediction_count = 0
        self.empty_cache_every = int(os.getenv("DISCOCS_MUQ_EMPTY_CACHE_EVERY", "0") or 0)

    def extract_track_vector(self, path: Path) -> np.ndarray:
        logger.info(
            "Extracting MuQ-MuLan track vector path=%s model=%s pretrained=%s device=%s",
            path,
            self.model_name,
            self.pretrained_name,
            self.device,
        )
        audio = load_audio_with_ffmpeg(path, sample_rate=self.sample_rate)
        embedding = self._predict(audio)
        return pool_and_normalize(np.asarray(embedding, dtype=np.float32))

    def extract_text_vector(self, text: str) -> np.ndarray:
        query = text.strip()
        if not query:
            raise ValueError("Text query is empty")
        model, torch, _device = self._ensure_model()
        output = None
        try:
            inference_context = torch.inference_mode if hasattr(torch, "inference_mode") else torch.no_grad
            with inference_context():
                if hasattr(model, "extract_text_latents"):
                    output = model.extract_text_latents([query])
                elif hasattr(model, "get_text_embedding"):
                    output = model.get_text_embedding([query])
                elif hasattr(model, "encode_text"):
                    output = model.encode_text([query])
                elif hasattr(model, "get_text_embeds"):
                    output = model.get_text_embeds([query])
                elif hasattr(model, "text_embedding"):
                    output = model.text_embedding([query])
                else:
                    try:
                        output = model(texts=[query])
                    except TypeError:
                        try:
                            output = model(text=[query])
                        except TypeError as exc:
                            raise RuntimeError(
                                "MuQ-MuLan text embedding API was not found. Expected one of "
                                "extract_text_latents, get_text_embedding, encode_text, get_text_embeds, text_embedding, "
                                "or a callable model accepting texts=[...]."
                            ) from exc
            return pool_and_normalize(self._embedding_output_to_array(output))
        finally:
            del output

    def _predict(self, audio: np.ndarray) -> np.ndarray:
        model, torch, device = self._ensure_model()
        tensor = None
        output = None
        try:
            tensor = torch.from_numpy(np.array(audio, dtype=np.float32, copy=True)).to(device)
            if tensor.ndim == 1:
                tensor = tensor.unsqueeze(0)
            inference_context = torch.inference_mode if hasattr(torch, "inference_mode") else torch.no_grad
            with inference_context():
                if hasattr(model, "get_audio_embedding"):
                    output = model.get_audio_embedding(tensor)
                elif hasattr(model, "encode_audio"):
                    output = model.encode_audio(tensor)
                elif hasattr(model, "extract_audio_latents"):
                    output = model.extract_audio_latents(tensor)
                else:
                    try:
                        output = model(wavs=tensor)
                    except TypeError:
                        output = model(tensor)
            return self._embedding_output_to_array(output)
        finally:
            del tensor
            del output
            self._prediction_count += 1
            if (
                self.empty_cache_every > 0
                and self._resolved_device == "cuda"
                and self._prediction_count % self.empty_cache_every == 0
            ):
                torch.cuda.empty_cache()

    def _embedding_output_to_array(self, output) -> np.ndarray:
        if isinstance(output, (tuple, list)):
            output = output[0]
        if isinstance(output, dict):
            for key in (
                "audio_embeds",
                "audio_embedding",
                "text_embeds",
                "text_embedding",
                "embedding",
                "embeddings",
                "last_hidden_state",
            ):
                if key in output:
                    output = output[key]
                    break
            else:
                raise RuntimeError("MuQ-MuLan output did not include an embedding tensor")
        if hasattr(output, "detach"):
            output = output.detach().cpu().numpy()
        array = np.asarray(output, dtype=np.float32)
        if array.ndim == 2 and array.shape[0] == 1:
            array = array[0]
        return array.reshape(-1)

    def _ensure_model(self):
        if self._model is not None:
            return self._model, self._torch, self._resolved_device
        os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")
        _ensure_int_max_str_digits_compat(sys)
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError(
                "torch is required for MuQ-MuLan inference. Install with the muq optional dependencies."
            ) from exc
        try:
            from muq import MuQMuLan
        except ImportError as exc:
            raise RuntimeError(
                "muq is required for MuQ-MuLan inference. Install with the muq optional dependencies."
            ) from exc
        resolved_device = self._resolve_device(torch)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, object] = {}
        if self.cache_dir:
            kwargs["cache_dir"] = str(self.cache_dir)
        try:
            model = MuQMuLan.from_pretrained(self.pretrained_name, **kwargs)
        except Exception as exc:
            raise RuntimeError(
                "MuQ-MuLan model could not be loaded. If this is the first run, allow Hugging Face "
                f"download access or pre-populate {self.cache_dir}. Original error: {exc}"
            ) from exc
        model = model.to(resolved_device)
        model.eval()
        self._model = model
        self._torch = torch
        self._resolved_device = resolved_device
        logger.info(
            "Loaded MuQ-MuLan model pretrained=%s cache_dir=%s device=%s",
            self.pretrained_name,
            self.cache_dir,
            resolved_device,
        )
        return self._model, self._torch, self._resolved_device

    def _resolve_device(self, torch):
        if self.device == "auto":
            return "cuda" if torch.cuda.is_available() else "cpu"
        if self.device not in {"cpu", "cuda"}:
            raise ValueError("DISCOCS_MUQ_DEVICE must be 'auto', 'cpu', or 'cuda'")
        if self.device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("DISCOCS_MUQ_DEVICE=cuda but torch reports no CUDA device")
        return self.device


def download_muq_mulan_model(settings: Settings) -> Path:
    embedder = MuqMulanEmbedder(settings)
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError(
            "huggingface_hub is required to download MuQ-MuLan. Install with the muq optional dependencies."
        ) from exc
    embedder.cache_dir.mkdir(parents=True, exist_ok=True)
    repos = [embedder.pretrained_name]
    for repo_id in MUQ_MULAN_REQUIRED_REPOS:
        if repo_id not in repos:
            repos.append(repo_id)
    for repo_id in repos:
        logger.info("Downloading MuQ-MuLan Hugging Face snapshot repo=%s cache_dir=%s", repo_id, embedder.cache_dir)
        snapshot_download(
            repo_id=repo_id,
            cache_dir=str(embedder.cache_dir),
            resume_download=True,
        )
    return embedder.cache_dir


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


def load_audio_with_ffmpeg(path: Path, sample_rate: int = 16000) -> np.ndarray:
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
        str(sample_rate),
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


def _l2_normalize(vector: np.ndarray) -> np.ndarray:
    pooled = vector.astype(np.float32)
    norm = np.linalg.norm(pooled)
    if norm == 0:
        raise ValueError("Embedding vector has zero norm")
    return (pooled / norm).astype(np.float32)


def blend_embeddings(vectors: list[np.ndarray] | np.ndarray) -> np.ndarray:
    if isinstance(vectors, list):
        if not vectors:
            raise ValueError("No vectors to blend")
        stacked = np.stack([np.asarray(vector, dtype=np.float32).reshape(-1) for vector in vectors], axis=0)
    else:
        array = np.asarray(vectors, dtype=np.float32)
        if array.ndim == 1:
            return _l2_normalize(array)
        if array.ndim != 2:
            raise ValueError(f"Expected 1D or 2D embeddings, got shape {array.shape}")
        if array.shape[0] == 0:
            raise ValueError("No vectors to blend")
        stacked = array
    return _l2_normalize(stacked.mean(axis=0))


def pool_and_normalize(embeddings: np.ndarray) -> np.ndarray:
    if embeddings.ndim == 1:
        pooled = embeddings.astype(np.float32)
    elif embeddings.ndim == 2:
        pooled = embeddings.mean(axis=0).astype(np.float32)
    else:
        raise ValueError(f"Expected 1D or 2D embeddings, got shape {embeddings.shape}")
    return _l2_normalize(pooled)
