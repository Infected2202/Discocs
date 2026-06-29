"""Tests for session negative feedback (skip-centroid penalty).

Inference-time analog of contrastive negative feedback (Deezer): Flow steers
candidate selection away from the centroid of tracks the user skipped this
session. Skips are noisy, so the penalty is a graded nudge above a similarity
threshold — not a ban.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np

from app.services.flow_candidates import (
    FlowSessionContext,
    _negative_centroid,
    score_candidates,
)


def _store_no_prefs() -> MagicMock:
    """Store whose user_track_preferences query returns nothing."""
    store = MagicMock()
    conn_ctx = MagicMock()
    conn_ctx.__enter__ = lambda s: conn_ctx
    conn_ctx.__exit__ = MagicMock(return_value=False)
    conn_ctx.execute.return_value.fetchall.return_value = []
    store.connect.return_value = conn_ctx
    return store


def _session_with_skip(skip_vec: np.ndarray) -> FlowSessionContext:
    s = FlowSessionContext(session_id="s1", region_id="r1")
    s.recent_skipped_vectors = [skip_vec.astype(np.float32)]
    return s


# ---------------------------------------------------------------------------
# _negative_centroid
# ---------------------------------------------------------------------------

def test_negative_centroid_none_without_skips():
    s = FlowSessionContext(session_id="s1", region_id="r1")
    assert _negative_centroid(s) is None


def test_negative_centroid_normalised():
    s = _session_with_skip(np.array([3.0, 4.0], dtype=np.float32))
    c = _negative_centroid(s)
    assert c is not None
    assert abs(float(np.linalg.norm(c)) - 1.0) < 1e-5


# ---------------------------------------------------------------------------
# Penalty in score_candidates
# ---------------------------------------------------------------------------

def test_candidate_near_skip_centroid_is_penalised():
    skip = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    session = _session_with_skip(skip)

    # Two candidates with identical region_fit; A sits on the skip direction,
    # B is orthogonal to it.
    candidates = [
        (10, 0.9, 1, None),   # near skip
        (20, 0.9, 2, None),   # far from skip
    ]
    vecs = {
        10: np.array([1.0, 0.0, 0.0], dtype=np.float32),   # cosine 1.0 to skip
        20: np.array([0.0, 1.0, 0.0], dtype=np.float32),   # cosine 0.0 to skip
    }

    with patch("app.services.flow_candidates.load_embeddings_batch", return_value=vecs):
        scored = score_candidates(candidates, _store_no_prefs(), session)

    by_id = {c.track_id: c for c in scored}
    assert by_id[10].negative_penalty > 0.0
    assert by_id[20].negative_penalty == 0.0
    # The skip-aligned candidate must rank below the orthogonal one.
    assert by_id[10].final_score < by_id[20].final_score
    assert "negative_penalty" in by_id[10].score_breakdown


def test_penalty_zero_below_threshold():
    # Candidate moderately similar (cosine ~0.45 < 0.50 threshold) → no penalty.
    skip = np.array([1.0, 0.0], dtype=np.float32)
    session = _session_with_skip(skip)
    # vector at ~63° from skip → cosine ~0.45
    v = np.array([0.45, np.sqrt(1 - 0.45**2)], dtype=np.float32)
    candidates = [(10, 0.8, 1, None)]

    with patch("app.services.flow_candidates.load_embeddings_batch", return_value={10: v}):
        scored = score_candidates(candidates, _store_no_prefs(), session)

    assert scored[0].negative_penalty == 0.0


def test_penalty_graded_with_similarity():
    skip = np.array([1.0, 0.0], dtype=np.float32)
    session = _session_with_skip(skip)
    # cosine 1.0 (full) vs cosine 0.75 (partial, above threshold)
    near = np.array([1.0, 0.0], dtype=np.float32)
    mid = np.array([0.75, np.sqrt(1 - 0.75**2)], dtype=np.float32)
    candidates = [(10, 0.8, 1, None), (20, 0.8, 2, None)]

    with patch("app.services.flow_candidates.load_embeddings_batch",
               return_value={10: near, 20: mid}):
        scored = score_candidates(candidates, _store_no_prefs(), session)

    by_id = {c.track_id: c for c in scored}
    # Closer to skip territory → larger penalty.
    assert by_id[10].negative_penalty > by_id[20].negative_penalty > 0.0


def test_no_penalty_without_skips():
    session = FlowSessionContext(session_id="s1", region_id="r1")  # no skips
    candidates = [(10, 0.8, 1, None)]
    # load_embeddings_batch should not even be needed; return empty to be safe.
    with patch("app.services.flow_candidates.load_embeddings_batch", return_value={}):
        scored = score_candidates(candidates, _store_no_prefs(), session)
    assert scored[0].negative_penalty == 0.0
