from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
from urllib.request import urlretrieve

import numpy as np

from app.config import DISCOGS_EFFNET_MODEL, MODEL_FILES, Settings
from app.embedder import DiscogsEffnetEmbedder, configure_tensorflow_logging


HEAD_PACK_NAME = "discogs-effnet-heads"
HEAD_INPUT = "serving_default_model_Placeholder"
HEAD_OUTPUT = "PartitionedCall:0"
HEAD_TOP_N = 20
HEAD_TENSOR_FALLBACKS = (
    ("model/Placeholder", "model/Sigmoid"),
    ("model/Placeholder", "model/Softmax"),
    ("model/Placeholder", "model/Identity"),
)
CLASSIFICATION_HEAD_BASE_URL = "https://essentia.upf.edu/models/classification-heads"
FEATURE_EXTRACTOR_BASE_URL = "https://essentia.upf.edu/models/feature-extractors/discogs-effnet"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HeadModel:
    id: str
    folder: str
    filename: str
    metadata_filename: str | None
    output_kind: str
    top_n: int = HEAD_TOP_N
    base_model: str = DISCOGS_EFFNET_MODEL
    input_tensor: str = HEAD_INPUT
    output_tensor: str = HEAD_OUTPUT

    @property
    def source_url(self) -> str:
        return f"{CLASSIFICATION_HEAD_BASE_URL}/{self.folder}/{self.filename}"

    @property
    def metadata_url(self) -> str | None:
        if self.metadata_filename is None:
            return None
        return f"{CLASSIFICATION_HEAD_BASE_URL}/{self.folder}/{self.metadata_filename}"


@dataclass(frozen=True)
class Prediction:
    label: str
    score: float
    rank: int


@dataclass(frozen=True)
class HeadOutput:
    model_name: str
    scores: np.ndarray
    aggregation: str
    predictions: list[Prediction]


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    source_url: str
    downloaded: bool


def _head(
    model_id: str,
    folder: str,
    filename: str,
    output_kind: str,
    metadata_filename: str | None = None,
) -> HeadModel:
    return HeadModel(
        id=model_id,
        folder=folder,
        filename=filename,
        metadata_filename=metadata_filename or filename.replace(".pb", ".json"),
        output_kind=output_kind,
    )


DISCOGS_EFFNET_HEADS: tuple[HeadModel, ...] = (
    _head("genre_discogs400", "genre_discogs400", "genre_discogs400-discogs-effnet-1.pb", "multilabel"),
    _head("mtg_jamendo_genre", "mtg_jamendo_genre", "mtg_jamendo_genre-discogs-effnet-1.pb", "multilabel"),
    _head("mtg_jamendo_moodtheme", "mtg_jamendo_moodtheme", "mtg_jamendo_moodtheme-discogs-effnet-1.pb", "multilabel"),
    _head("mtg_jamendo_instrument", "mtg_jamendo_instrument", "mtg_jamendo_instrument-discogs-effnet-1.pb", "multilabel"),
    _head("mtg_jamendo_top50tags", "mtg_jamendo_top50tags", "mtg_jamendo_top50tags-discogs-effnet-1.pb", "multilabel"),
    _head("danceability", "danceability", "danceability-discogs-effnet-1.pb", "binary"),
    _head("voice_instrumental", "voice_instrumental", "voice_instrumental-discogs-effnet-1.pb", "binary"),
    _head("tonal_atonal", "tonal_atonal", "tonal_atonal-discogs-effnet-1.pb", "binary"),
    _head("approachability_2c", "approachability", "approachability_2c-discogs-effnet-1.pb", "binary"),
    _head("approachability_3c", "approachability", "approachability_3c-discogs-effnet-1.pb", "multiclass"),
    _head("approachability_regression", "approachability", "approachability_regression-discogs-effnet-1.pb", "regression"),
    _head("engagement_2c", "engagement", "engagement_2c-discogs-effnet-1.pb", "binary"),
    _head("engagement_3c", "engagement", "engagement_3c-discogs-effnet-1.pb", "multiclass"),
    _head("engagement_regression", "engagement", "engagement_regression-discogs-effnet-1.pb", "regression"),
    _head("timbre", "timbre", "timbre-discogs-effnet-1.pb", "multiclass"),
    _head("nsynth_instrument", "nsynth_instrument", "nsynth_instrument-discogs-effnet-1.pb", "multiclass"),
    _head("nsynth_acoustic_electronic", "nsynth_acoustic_electronic", "nsynth_acoustic_electronic-discogs-effnet-1.pb", "binary"),
    _head("nsynth_bright_dark", "nsynth_bright_dark", "nsynth_bright_dark-discogs-effnet-1.pb", "binary"),
    _head("nsynth_reverb", "nsynth_reverb", "nsynth_reverb-discogs-effnet-1.pb", "binary"),
    _head("mood_acoustic", "mood_acoustic", "mood_acoustic-discogs-effnet-1.pb", "binary"),
    _head("mood_aggressive", "mood_aggressive", "mood_aggressive-discogs-effnet-1.pb", "binary"),
    _head("mood_electronic", "mood_electronic", "mood_electronic-discogs-effnet-1.pb", "binary"),
    _head("mood_happy", "mood_happy", "mood_happy-discogs-effnet-1.pb", "binary"),
    _head("mood_party", "mood_party", "mood_party-discogs-effnet-1.pb", "binary"),
    _head("mood_relaxed", "mood_relaxed", "mood_relaxed-discogs-effnet-1.pb", "binary"),
    _head("mood_sad", "mood_sad", "mood_sad-discogs-effnet-1.pb", "binary"),
    _head("genre_electronic", "genre_electronic", "genre_electronic-discogs-effnet-1.pb", "multiclass"),
    _head("genre_dortmund", "genre_dortmund", "genre_dortmund-discogs-effnet-1.pb", "multiclass"),
    _head("genre_rosamerica", "genre_rosamerica", "genre_rosamerica-discogs-effnet-1.pb", "multiclass"),
    _head("genre_tzanetakis", "genre_tzanetakis", "genre_tzanetakis-discogs-effnet-1.pb", "multiclass"),
    _head("fma_small", "fma_small", "fma_small-discogs-effnet-1.pb", "multiclass"),
)


HEAD_MODELS = {head.id: head for head in DISCOGS_EFFNET_HEADS}


class DiscogsEffnetHeadPackAnalyzer:
    def __init__(
        self,
        settings: Settings,
        heads: tuple[HeadModel, ...] = DISCOGS_EFFNET_HEADS,
    ):
        self.settings = settings
        self.heads = heads
        self.base_embedder = DiscogsEffnetEmbedder(settings, DISCOGS_EFFNET_MODEL)
        self._models: dict[str, object] = {}
        self._labels: dict[str, list[str]] = {}

    def analyze_track(self, path: Path) -> list[HeadOutput]:
        logger.info("Analyzing Discogs-EffNet heads path=%s heads=%s", path, len(self.heads))
        patch_embeddings = self.base_embedder.extract_patch_embeddings(path)
        return self.analyze_patch_embeddings(patch_embeddings)

    def extract_patch_embeddings(self, path: Path) -> np.ndarray:
        return np.asarray(self.base_embedder.extract_patch_embeddings(path), dtype=np.float32)

    def analyze_patch_embeddings(self, patch_embeddings: np.ndarray) -> list[HeadOutput]:
        return [self._run_head(head, patch_embeddings) for head in self.heads]

    def analyze_patch_embedding_batch(self, patch_embeddings_by_track: list[np.ndarray]) -> list[list[HeadOutput]]:
        if not patch_embeddings_by_track:
            return []
        patch_counts = [int(len(patches)) for patches in patch_embeddings_by_track]
        if any(count <= 0 for count in patch_counts):
            raise ValueError("No EffNet patches extracted")
        combined = np.concatenate(
            [np.asarray(patches, dtype=np.float32) for patches in patch_embeddings_by_track],
            axis=0,
        )
        outputs_by_track: list[list[HeadOutput]] = [[] for _ in patch_embeddings_by_track]
        for head in self.heads:
            predictions = np.asarray(self._predictor(head)(combined), dtype=np.float32)
            offset = 0
            for index, patch_count in enumerate(patch_counts):
                track_predictions = predictions[offset : offset + patch_count]
                offset += patch_count
                outputs_by_track[index].append(self._head_output(head, track_predictions))
        return outputs_by_track

    def _run_head(self, head: HeadModel, patch_embeddings: np.ndarray) -> HeadOutput:
        predictions = np.asarray(
            self._predictor(head)(np.asarray(patch_embeddings, dtype=np.float32)),
            dtype=np.float32,
        )
        return self._head_output(head, predictions)

    def _head_output(self, head: HeadModel, predictions: np.ndarray) -> HeadOutput:
        scores = aggregate_scores(predictions)
        if head.output_kind == "regression":
            top = []
        else:
            labels = self._labels_for(head, scores.shape[0])
            top = top_predictions(scores, labels, head.top_n)
        return HeadOutput(
            model_name=head.id,
            scores=scores,
            aggregation="mean_patches",
            predictions=top,
        )

    def _predictor(self, head: HeadModel):
        configure_tensorflow_logging()
        if head.id not in self._models:
            model_path = self.settings.model_dir / head.filename
            if not model_path.exists():
                logger.error("Head model file not found head=%s path=%s", head.id, model_path)
                raise FileNotFoundError(f"Model file not found: {model_path}")
            try:
                from essentia.standard import TensorflowPredict2D
            except ImportError as exc:
                logger.error("Essentia missing for head inference head=%s", head.id)
                raise RuntimeError(
                    "essentia-tensorflow is required for Discogs head inference"
                ) from exc
            logger.info("Loading head model head=%s path=%s", head.id, model_path)
            errors = []
            for input_tensor, output_tensor in tensor_candidates(head):
                try:
                    self._models[head.id] = TensorflowPredict2D(
                        graphFilename=str(model_path),
                        input=input_tensor,
                        output=output_tensor,
                    )
                    if (input_tensor, output_tensor) != (head.input_tensor, head.output_tensor):
                        logger.warning(
                            "Loaded head model with fallback tensors head=%s input=%s output=%s",
                            head.id,
                            input_tensor,
                            output_tensor,
                        )
                    break
                except RuntimeError as exc:
                    errors.append(f"input={input_tensor} output={output_tensor}: {exc}")
            else:
                attempts = "; ".join(errors)
                raise RuntimeError(
                    f"Failed to configure head {head.id} ({model_path}). "
                    f"Attempted tensor pairs: {attempts}"
                )
        return self._models[head.id]

    def _labels_for(self, head: HeadModel, dim: int) -> list[str]:
        if head.id not in self._labels:
            if head.metadata_filename is not None:
                path = self.settings.model_dir / head.metadata_filename
                if path.exists():
                    self._labels[head.id] = load_model_classes(path)
                else:
                    self._labels[head.id] = fallback_labels(head, dim)
            else:
                self._labels[head.id] = fallback_labels(head, dim)
        labels = self._labels[head.id]
        if len(labels) != dim:
            raise ValueError(
                f"Head {head.id} returned {dim} scores but has {len(labels)} labels"
            )
        return labels


def required_model_files(heads: tuple[HeadModel, ...] = DISCOGS_EFFNET_HEADS) -> list[tuple[str, str]]:
    files = [
        (
            "discogs-effnet-bs64-1.pb",
            f"{FEATURE_EXTRACTOR_BASE_URL}/discogs-effnet-bs64-1.pb",
        )
    ]
    for head in heads:
        files.append((head.filename, head.source_url))
        if head.metadata_filename and head.metadata_url:
            files.append((head.metadata_filename, head.metadata_url))
    return files


def known_model_files(
    settings: Settings,
    heads: tuple[HeadModel, ...] = DISCOGS_EFFNET_HEADS,
) -> list[dict[str, object]]:
    by_filename: dict[str, dict[str, object]] = {}
    for model_id, filename in MODEL_FILES.items():
        source_url = f"{FEATURE_EXTRACTOR_BASE_URL}/{filename}"
        by_filename[filename] = {
            "model": model_id,
            "filename": filename,
            "path": str(settings.model_dir / filename),
            "source_url": source_url,
            "kind": "base" if model_id == DISCOGS_EFFNET_MODEL else "embedding",
            "ready": (settings.model_dir / filename).exists(),
        }
    for filename, source_url in required_model_files(heads):
        current = by_filename.get(filename)
        if current is None:
            by_filename[filename] = {
                "model": filename.replace(".pb", "").replace(".json", ""),
                "filename": filename,
                "path": str(settings.model_dir / filename),
                "source_url": source_url,
                "kind": "head-pack",
                "ready": (settings.model_dir / filename).exists(),
            }
        else:
            current["source_url"] = source_url
            current["kind"] = "base" if filename == MODEL_FILES[DISCOGS_EFFNET_MODEL] else current["kind"]
            current["ready"] = (settings.model_dir / filename).exists()
    return sorted(by_filename.values(), key=lambda item: (str(item["kind"]), str(item["filename"])))


def download_model_file(settings: Settings, filename: str, source_url: str) -> DownloadResult:
    settings.model_dir.mkdir(parents=True, exist_ok=True)
    path = settings.model_dir / filename
    if path.exists():
        if path.suffix == ".json":
            load_metadata_json(path)
        logger.info("Model file already present path=%s", path)
        return DownloadResult(path=path, source_url=source_url, downloaded=False)
    logger.info("Downloading model file path=%s url=%s", path, source_url)
    urlretrieve(source_url, path)
    if path.suffix == ".json":
        load_metadata_json(path)
    return DownloadResult(path=path, source_url=source_url, downloaded=True)


def download_head_pack_models(
    settings: Settings,
    heads: tuple[HeadModel, ...] = DISCOGS_EFFNET_HEADS,
) -> list[DownloadResult]:
    results: list[DownloadResult] = []
    for filename, source_url in required_model_files(heads):
        results.append(download_model_file(settings, filename, source_url))
    return results


def head_pack_readiness(settings: Settings) -> dict[str, object]:
    files = []
    for filename, source_url in required_model_files():
        path = settings.model_dir / filename
        files.append(
            {
                "filename": filename,
                "path": str(path),
                "source_url": source_url,
                "ready": path.exists(),
            }
        )
    missing = [file["filename"] for file in files if not file["ready"]]
    ready_files = [file["filename"] for file in files if file["ready"]]
    per_head = []
    for head in DISCOGS_EFFNET_HEADS:
        names = [head.filename]
        if head.metadata_filename:
            names.append(head.metadata_filename)
        per_head.append(
            {
                "id": head.id,
                "output_kind": head.output_kind,
                "files": names,
                "ready": all((settings.model_dir / name).exists() for name in names),
            }
        )
    return {
        "pack": HEAD_PACK_NAME,
        "models": [head.id for head in DISCOGS_EFFNET_HEADS],
        "heads": per_head,
        "model_files": known_model_files(settings),
        "files": files,
        "required_files": len(files),
        "ready_files": ready_files,
        "ready_file_count": len(ready_files),
        "missing_files": missing,
        "ready": not missing,
    }


def load_model_classes(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Model metadata file not found: {path}")
    metadata = load_metadata_json(path)
    classes = metadata.get("classes")
    if not isinstance(classes, list) or not all(isinstance(item, str) for item in classes):
        raise ValueError(f"Model metadata does not contain a string classes list: {path}")
    return classes


def load_metadata_json(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as file:
        metadata = json.load(file)
    if not isinstance(metadata, dict):
        raise ValueError(f"Model metadata is not a JSON object: {path}")
    return metadata


def fallback_labels(head: HeadModel, dim: int) -> list[str]:
    return [f"{head.id}_{index}" for index in range(dim)]


def tensor_candidates(head: HeadModel) -> list[tuple[str, str]]:
    candidates = [(head.input_tensor, head.output_tensor)]
    for candidate in HEAD_TENSOR_FALLBACKS:
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def aggregate_scores(scores: np.ndarray) -> np.ndarray:
    if scores.ndim == 1:
        aggregated = scores.astype(np.float32)
    elif scores.ndim == 2:
        aggregated = scores.mean(axis=0).astype(np.float32)
    else:
        raise ValueError(f"Expected 1D or 2D prediction scores, got shape {scores.shape}")
    if aggregated.size == 0:
        raise ValueError("Prediction scores are empty")
    return aggregated


def top_predictions(scores: np.ndarray, labels: list[str], top_n: int) -> list[Prediction]:
    if scores.shape[0] != len(labels):
        raise ValueError(
            f"Prediction score count {scores.shape[0]} does not match label count {len(labels)}"
        )
    limit = min(top_n, len(labels))
    order = np.argsort(scores)[::-1][:limit]
    return [
        Prediction(label=labels[int(index)], score=float(scores[int(index)]), rank=rank)
        for rank, index in enumerate(order, start=1)
    ]
