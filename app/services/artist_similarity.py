"""Fast artist candidate search with release-catalog reranking."""
from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from app.store import Store

_CACHE_TTL_SECONDS = 600
_CANDIDATE_POOL = 200
_CENTROID_WEIGHT = 0.60
_CATALOG_WEIGHT = 0.40

_cache_lock = Lock()
_cache: dict[tuple[str, str], tuple[float, np.ndarray, np.ndarray]] = {}


@dataclass(frozen=True)
class ArtistSimilarityResult:
    artist_id: int
    score: float
    centroid_similarity: float
    catalog_similarity: float


def _get_matrix(store: Store, model_name: str) -> tuple[np.ndarray, np.ndarray]:
    now = time.monotonic()
    cache_key = (str(store.db_path.resolve()), model_name)
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1], cached[2]
    ids, matrix = store.load_all_artist_embeddings(model_name)
    if matrix.shape[0] > 0:
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        matrix = matrix / np.where(norms == 0, 1.0, norms)
    with _cache_lock:
        _cache[cache_key] = (now, ids, matrix)
    return ids, matrix


def invalidate_cache(model_name: str | None = None) -> None:
    with _cache_lock:
        if model_name is None:
            _cache.clear()
        else:
            for key in [key for key in _cache if key[1] == model_name]:
                _cache.pop(key, None)


def _normalise_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0, 1.0, norms)


def catalog_similarity(first: np.ndarray, second: np.ndarray) -> float:
    """Symmetric mean of each release's best match in the other catalog."""
    if first.shape[0] == 0 or second.shape[0] == 0:
        return 0.0
    similarities = _normalise_rows(first) @ _normalise_rows(second).T
    first_to_second = float(np.max(similarities, axis=1).mean())
    second_to_first = float(np.max(similarities, axis=0).mean())
    return (first_to_second + second_to_first) / 2.0


def find_similar_artists(
    store: Store,
    model_name: str,
    source_artist_id: int,
    *,
    limit: int = 16,
    candidate_pool: int = _CANDIDATE_POOL,
) -> list[ArtistSimilarityResult]:
    """Select by artist centroid, then rerank by symmetric catalog coverage."""
    source_vector = store.load_artist_embedding(source_artist_id, model_name)
    if source_vector is None:
        return []
    ids, matrix = _get_matrix(store, model_name)
    if matrix.shape[0] == 0:
        return []

    source = np.asarray(source_vector, dtype=np.float32)
    norm = float(np.linalg.norm(source))
    if norm == 0:
        return []
    centroid_scores = matrix @ (source / norm)
    centroid_scores[ids == source_artist_id] = -1.0
    ranked_indices = np.argsort(centroid_scores)[::-1]
    pool_indices = [
        int(index) for index in ranked_indices
        if float(centroid_scores[index]) > 0
    ][:max(limit, candidate_pool)]
    if not pool_indices:
        return []

    candidate_ids = [int(ids[index]) for index in pool_indices]
    catalogs = store.load_release_embeddings_for_artists(
        [source_artist_id, *candidate_ids], model_name
    )
    source_catalog = catalogs.get(source_artist_id)
    if source_catalog is None:
        return []

    results: list[ArtistSimilarityResult] = []
    for index, artist_id in zip(pool_indices, candidate_ids, strict=True):
        candidate_catalog = catalogs.get(artist_id)
        if candidate_catalog is None:
            continue
        centroid_similarity = float(centroid_scores[index])
        catalog_score = catalog_similarity(source_catalog, candidate_catalog)
        score = _CENTROID_WEIGHT * centroid_similarity + _CATALOG_WEIGHT * catalog_score
        if score <= 0:
            continue
        results.append(ArtistSimilarityResult(
            artist_id=artist_id,
            score=score,
            centroid_similarity=centroid_similarity,
            catalog_similarity=catalog_score,
        ))
    results.sort(key=lambda item: (-item.score, item.artist_id))
    return results[:limit]
