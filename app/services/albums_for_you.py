"""Precompute 'Albums For You' dashboard shelf.

Uses the same scoring logic as release_scoring but in two efficient phases:
1. Matrix multiplication to get top-200 candidates (~1ms)
2. Full scoring only for those 200 candidates (~200 DB queries vs 7000×3)
3. Result stored in DB cache; dashboard reads it instantly.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import numpy as np

from app.serializers.search import _release_shelf_item
from app.services.release_scoring import ReleaseRecommendationSettings, score_release
from app.services.release_similarity import _get_matrix

if TYPE_CHECKING:
    from app.store import Store

logger = logging.getLogger(__name__)

_CANDIDATE_POOL = 200  # run full scoring on top N by cosine
_MAX_PER_ARTIST = 3


def compute_albums_for_you(
    store: Store,
    model_name: str,
    limit: int = 50,
) -> list[dict[str, object]]:
    """Compute scored album recommendations for the current user.

    Returns shelf items ready for JSON serialisation.
    """
    # --- Phase 1: taste centroid from positive tracks --------------------
    positive_ids = store.list_positive_track_ids_with_embeddings(
        model_name, min_score=1.0, limit=500
    )
    taste_vectors: list[np.ndarray] = []
    if positive_ids:
        # Batch load via load_embeddings (all model embeddings in one query),
        # then filter to positive_ids set.
        all_ids, all_vecs = store.load_embeddings(model_name)
        pos_set = set(positive_ids)
        mask = np.array([int(tid) in pos_set for tid in all_ids], dtype=bool)
        if mask.any():
            taste_vectors = list(all_vecs[mask])

    if not taste_vectors:
        logger.info("albums_for_you: no taste vectors, skipping")
        return []

    tc = np.mean(taste_vectors, axis=0).astype(np.float32)
    norm = float(np.linalg.norm(tc))
    if norm == 0:
        return []
    taste_centroid = tc / norm

    # --- Phase 2: matrix cosine → top candidates -------------------------
    ids, matrix = _get_matrix(store, model_name)
    if matrix.shape[0] == 0:
        return []

    scores = matrix @ taste_centroid
    k = min(_CANDIDATE_POOL, len(scores))
    top_idx = np.argpartition(scores, -k)[-k:]
    top_idx = top_idx[np.argsort(scores[top_idx])[::-1]]
    candidate_ids = [int(ids[i]) for i in top_idx]

    # --- Phase 3: full scoring on candidates only ------------------------
    settings = ReleaseRecommendationSettings()
    agg_map = {agg.release_id: agg for agg in store.list_ready_release_aggregates(model_name)}

    from app.models import utc_now  # noqa: PLC0415
    scored: list[tuple[float, int, list[str]]] = []
    for release_id in candidate_ids:
        agg = agg_map.get(release_id)
        if agg is None:
            continue
        centroid = store.load_release_embedding(release_id, model_name)
        release_pref = store.get_release_preference(release_id)
        track_pref_rows = store.list_release_track_preferences(release_id)
        rs = score_release(
            agg=agg,
            release_pref=release_pref,
            track_pref_rows=track_pref_rows,
            centroid=centroid,
            taste_centroid=taste_centroid,
            taste_vectors=taste_vectors,
            settings=settings,
        )
        scored.append((rs.score, release_id, rs.reasons))

    scored.sort(reverse=True)

    # --- Phase 4: per-artist cap + serialise -----------------------------
    artist_counts: dict[int, int] = {}
    items: list[dict[str, object]] = []
    for _score, release_id, reasons in scored:
        if len(items) >= limit:
            break
        artists = store.artist_ids_for_release(release_id)
        if any(artist_counts.get(aid, 0) >= _MAX_PER_ARTIST for aid in artists):
            continue
        release = store.get_release(release_id)
        if release is None:
            continue
        item = _release_shelf_item(release, reason=reasons[0] if reasons else None)
        for aid in artists:
            artist_counts[aid] = artist_counts.get(aid, 0) + 1
        items.append(item)

    return items


def refresh_albums_for_you(store: Store, model_name: str) -> int:
    """Compute and persist albums_for_you cache. Returns number of items saved."""
    items = compute_albums_for_you(store, model_name)
    store.set_albums_for_you_cache(model_name, json.dumps(items))
    logger.info("albums_for_you: cached %d items model=%s", len(items), model_name)
    return len(items)
