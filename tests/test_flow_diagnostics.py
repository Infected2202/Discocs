"""Tests for Flow candidate pool diagnostics (pool_sources breakdown)."""
from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.services.flow_candidates import (
    FlowSessionContext,
    PoolDiagnostics,
    build_candidate_pool,
)


# ---------------------------------------------------------------------------
# Minimal fakes
# ---------------------------------------------------------------------------

def _make_region(region_id: str = "r1") -> MagicMock:
    r = MagicMock()
    r.id = region_id
    return r


def _make_store(
    centroid: np.ndarray | None = None,
    seed_track_ids: list[int] | None = None,
    liked_track_ids: list[int] | None = None,
) -> MagicMock:
    store = MagicMock()
    store.load_flow_region_embedding.return_value = centroid

    def _region_tracks(region_id: str, role: str) -> list[MagicMock]:
        if role in ("seed", "representative") and seed_track_ids:
            return [_rt(tid) for tid in seed_track_ids]
        return []

    store.list_flow_region_tracks.side_effect = _region_tracks

    # Liked tracks via SQL — return as row-like objects
    liked = [[tid] for tid in (liked_track_ids or [])]
    conn_ctx = MagicMock()
    conn_ctx.__enter__ = lambda s: conn_ctx
    conn_ctx.__exit__ = MagicMock(return_value=False)
    conn_ctx.execute.return_value.fetchall.return_value = [
        {"track_id": tid} for tid in (liked_track_ids or [])
    ]
    store.connect.return_value = conn_ctx

    return store


def _rt(track_id: int) -> MagicMock:
    rt = MagicMock()
    rt.track_id = track_id
    return rt


def _session(model_key: str = "discogs_multi") -> FlowSessionContext:
    return FlowSessionContext(session_id="s1", region_id="r1", model_key=model_key)


# ---------------------------------------------------------------------------
# Tests: diagnostics structure
# ---------------------------------------------------------------------------

def test_build_pool_returns_three_tuple():
    store = _make_store(seed_track_ids=[1, 2])
    result = build_candidate_pool(store, MagicMock(), _make_region(), _session())
    assert len(result) == 3
    pool, diag, source_map = result
    assert isinstance(diag, PoolDiagnostics)
    assert isinstance(source_map, dict)


def test_no_centroid_hnsw_skipped():
    store = _make_store(centroid=None, seed_track_ids=[10, 20])
    _, diag, source_map = build_candidate_pool(store, MagicMock(), _make_region(), _session())

    assert diag.centroid_loaded is False
    assert diag.hnsw_count == 0
    assert diag.hnsw_error is None
    # seeds should be added
    assert diag.seeds_added == 2
    assert source_map[10] == "seed"
    assert source_map[20] == "seed"


def test_hnsw_error_recorded():
    centroid = np.ones(64, dtype=np.float32)
    store = _make_store(centroid=centroid, seed_track_ids=[1])

    fake_instance = MagicMock()
    fake_instance.similar_vector.side_effect = FileNotFoundError("index not found")

    with patch("app.recommender.Recommender", return_value=fake_instance):
        _, diag, _ = build_candidate_pool(store, MagicMock(), _make_region(), _session())

    assert diag.centroid_loaded is True
    assert diag.hnsw_count == 0
    assert diag.hnsw_error is not None
    assert "index not found" in diag.hnsw_error


def test_hnsw_success_source_label():
    centroid = np.ones(64, dtype=np.float32)
    store = _make_store(centroid=centroid, seed_track_ids=[])

    fake_result = MagicMock()
    fake_result.track.id = 99
    fake_result.similarity = 0.91

    fake_instance = MagicMock()
    fake_instance.similar_vector.return_value = [fake_result]

    with patch("app.recommender.Recommender", return_value=fake_instance):
        pool, diag, source_map = build_candidate_pool(store, MagicMock(), _make_region(), _session())

    assert diag.hnsw_count == 1
    assert source_map[99] == "hnsw"
    assert any(tid == 99 for tid, _ in pool)


def test_liked_longterm_source_label():
    store = _make_store(centroid=None, seed_track_ids=[], liked_track_ids=[7, 8])
    _, diag, source_map = build_candidate_pool(store, MagicMock(), _make_region(), _session())

    assert diag.liked_added == 2
    assert source_map[7] == "liked_longterm"
    assert source_map[8] == "liked_longterm"


def test_played_tracks_excluded():
    session = _session()
    session.played_track_ids = {10, 20}
    store = _make_store(centroid=None, seed_track_ids=[10, 20, 30])
    pool, diag, source_map = build_candidate_pool(store, MagicMock(), _make_region(), session)

    pool_ids = {tid for tid, _ in pool}
    assert 10 not in pool_ids
    assert 20 not in pool_ids
    assert 30 in pool_ids


def test_diag_total_matches_pool_length():
    store = _make_store(centroid=None, seed_track_ids=[1, 2, 3])
    pool, diag, _ = build_candidate_pool(store, MagicMock(), _make_region(), _session())
    assert diag.total == len(pool)


def test_fallback_when_pool_empty():
    store = _make_store(centroid=None, seed_track_ids=[], liked_track_ids=[])
    pool, diag, source_map = build_candidate_pool(store, MagicMock(), _make_region(), _session())

    assert diag.fallback_used is True
    assert pool == []  # no seed tracks to fall back to either
