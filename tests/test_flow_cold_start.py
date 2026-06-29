"""Tests for Flow cold start: diversity sampling when there is no taste signal.

A new user / empty profile must still get a usable Flow. Instead of disabling
it, we spread anchors across the embedding space (farthest-point sampling),
cluster a library sample around them, and mark the regions as maximally
uncertain (seed_count=0) so the engine explores aggressively.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np

from app.services.flow_regions import (
    FlowSettings,
    _Region,
    _Seed,
    _build_cold_start_regions,
    _farthest_point_anchors,
    _weighted_centroid,
    rebuild_flow_profile,
)


def _unit(v) -> np.ndarray:
    a = np.array(v, dtype=np.float32)
    return a / np.linalg.norm(a)


# ---------------------------------------------------------------------------
# _farthest_point_anchors
# ---------------------------------------------------------------------------

def test_fps_returns_all_when_few_points():
    m = np.vstack([_unit([1, 0]), _unit([0, 1])]).astype(np.float32)
    assert _farthest_point_anchors(m, 5) == [0, 1]


def test_fps_spreads_across_clusters():
    # Three tight clusters around orthogonal axes; FPS with k=3 must pick one
    # anchor from each direction rather than clumping.
    rows = []
    for axis in ([1, 0, 0], [0, 1, 0], [0, 0, 1]):
        for _ in range(5):
            rows.append(_unit(axis))
    m = np.vstack(rows).astype(np.float32)
    anchors = _farthest_point_anchors(m, 3)
    dirs = {tuple(np.round(m[i]).tolist()) for i in anchors}
    # All three axis directions represented.
    assert len(dirs) == 3


# ---------------------------------------------------------------------------
# _build_cold_start_regions
# ---------------------------------------------------------------------------

def _store_returning_ids(ids: list[int]) -> MagicMock:
    store = MagicMock()
    conn_ctx = MagicMock()
    conn_ctx.__enter__ = lambda s: conn_ctx
    conn_ctx.__exit__ = MagicMock(return_value=False)
    conn_ctx.execute.return_value.fetchall.return_value = [{"id": i} for i in ids]
    store.connect.return_value = conn_ctx
    return store


def test_cold_start_regions_built_from_sample():
    ids = list(range(1, 16))
    store = _store_returning_ids(ids)
    # 3 orthogonal clusters of 5.
    vecs = {}
    for k, axis in enumerate(([1, 0, 0], [0, 1, 0], [0, 0, 1])):
        for j in range(5):
            vecs[ids[k * 5 + j]] = _unit(axis)

    settings = FlowSettings(cold_start_regions=3, cold_start_sample_size=100)
    with patch("app.services.flow_candidates.load_embeddings_batch", return_value=vecs):
        regions = _build_cold_start_regions(store, settings)

    assert len(regions) == 3
    for r in regions:
        assert r.centroid is not None
        assert abs(float(np.linalg.norm(r.centroid)) - 1.0) < 1e-5
        assert r.seeds  # has members
        assert all(s.weight == 1.0 for s in r.seeds)


def test_cold_start_regions_empty_sample():
    store = _store_returning_ids([])
    with patch("app.services.flow_candidates.load_embeddings_batch", return_value={}):
        regions = _build_cold_start_regions(store, FlowSettings())
    assert regions == []


# ---------------------------------------------------------------------------
# rebuild_flow_profile — cold-start branch
# ---------------------------------------------------------------------------

def test_rebuild_uses_cold_start_when_no_seeds():
    store = MagicMock()
    profile = MagicMock()
    profile.id = "p1"
    store.upsert_flow_profile.return_value = profile
    db_region = MagicMock()
    db_region.id = "reg1"
    store.upsert_flow_region.return_value = db_region

    region = _Region(index=0)
    region.seeds = [_Seed(1, _unit([1, 0]), 1.0), _Seed(2, _unit([0.95, 0.05]), 1.0)]
    region.centroid = _weighted_centroid(region.seeds)

    with patch("app.services.flow_regions._collect_seeds", return_value=[]), \
         patch("app.services.flow_regions._build_cold_start_regions", return_value=[region]), \
         patch("app.services.flow_regions._estimate_candidate_coverage", return_value=0):
        summary = rebuild_flow_profile(store, MagicMock(), FlowSettings())

    assert summary["status"] == "cold_start"
    assert summary["cold_start"] is True
    assert summary["region_count"] == 1

    # Profile finalised as cold_start.
    statuses = [c.args[1] for c in store.upsert_flow_profile.call_args_list]
    assert "cold_start" in statuses

    # Region persisted as uncertain: seed_count=0, weight=1.0.
    region_kwargs = store.upsert_flow_region.call_args.kwargs
    assert region_kwargs["seed_count"] == 0
    assert region_kwargs["weight"] == 1.0

    # No seed-role tracks persisted for a cold-start region.
    tracks = store.replace_flow_region_tracks.call_args.args[1]
    assert all(t.role == "representative" for t in tracks)


def test_rebuild_empty_when_no_seeds_and_no_library():
    store = MagicMock()
    profile = MagicMock()
    profile.id = "p1"
    store.upsert_flow_profile.return_value = profile

    with patch("app.services.flow_regions._collect_seeds", return_value=[]), \
         patch("app.services.flow_regions._build_cold_start_regions", return_value=[]):
        summary = rebuild_flow_profile(store, MagicMock(), FlowSettings())

    assert summary["status"] == "empty"
    statuses = [c.args[1] for c in store.upsert_flow_profile.call_args_list]
    assert "empty" in statuses
