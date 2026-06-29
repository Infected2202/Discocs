"""Fast release similarity via in-memory matrix cache.

Replaces the O(N×DB) rank_releases_for_user for release-page recommendations.
The matrix is loaded once and cached with a TTL; cosine similarity is a single
numpy matrix multiplication (~1ms for 7k releases).
"""
from __future__ import annotations

import logging
import time
from threading import Lock
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from app.store import Store

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 600  # 10 minutes

_cache_lock = Lock()
_cache: dict[str, tuple[float, np.ndarray, np.ndarray]] = {}
# key: model_name → (loaded_at, release_ids int64 array, matrix float32 N×D)


def _get_matrix(store: Store, model_name: str) -> tuple[np.ndarray, np.ndarray]:
    """Return (release_ids, matrix), loading from DB if cache is stale."""
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(model_name)
        if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1], cached[2]

    ids, matrix = store.load_all_release_embeddings(model_name)
    if matrix.shape[0] > 0:
        # Normalise rows so dot product == cosine similarity
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        matrix = matrix / norms
    with _cache_lock:
        _cache[model_name] = (now, ids, matrix)
    logger.debug("release_similarity: loaded matrix model=%s shape=%s", model_name, matrix.shape)
    return ids, matrix


def invalidate_cache(model_name: str | None = None) -> None:
    """Invalidate the in-memory matrix cache (call after new aggregates are saved)."""
    with _cache_lock:
        if model_name is None:
            _cache.clear()
        else:
            _cache.pop(model_name, None)


def find_similar_releases(
    store: Store,
    model_name: str,
    source_vector: np.ndarray,
    *,
    exclude_release_ids: set[int] | None = None,
    limit: int = 12,
) -> list[tuple[int, float]]:
    """Return list of (release_id, cosine_score) sorted by score desc.

    Pure vector similarity — no user preference adjustments.
    Suitable for the release-page "Similar Albums" shelf.
    """
    ids, matrix = _get_matrix(store, model_name)
    if matrix.shape[0] == 0:
        return []

    src = np.asarray(source_vector, dtype=np.float32)
    norm = float(np.linalg.norm(src))
    if norm == 0:
        return []
    src = src / norm

    scores = matrix @ src  # shape (N,)

    if exclude_release_ids:
        for rid in exclude_release_ids:
            mask = ids == rid
            scores[mask] = -1.0

    top_idx = np.argpartition(scores, -min(limit, len(scores)))[-limit:]
    top_idx = top_idx[np.argsort(scores[top_idx])[::-1]]

    return [(int(ids[i]), float(scores[i])) for i in top_idx if float(scores[i]) > 0]
