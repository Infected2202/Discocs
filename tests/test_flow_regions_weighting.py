"""Tests for consistency-driven seed weighting and weighted centroids.

Research basis (Deezer, UMAP 2025): a single play is noisy → near-floor weight;
reliability comes from consistent repeats. Weight must actually steer the
region centroid, so a play-only track contributes weakly instead of voting
equally with a liked track.
"""
from __future__ import annotations

import numpy as np

from app.services.flow_regions import (
    _WEAK_SEED_WEIGHT,
    _Seed,
    _cluster_seeds,
    _seed_weight,
    _weighted_centroid,
)


def _row(**kw) -> dict:
    base = {
        "liked": 0,
        "play_count": 0,
        "replay_count": 0,
        "completion_count": 0,
        "early_skip_count": 0,
        "score": 0.0,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# _seed_weight
# ---------------------------------------------------------------------------

def test_single_play_is_weak():
    w = _seed_weight(_row(play_count=1))
    # A single play must stay below the weak threshold — it never spawns a region.
    assert w < _WEAK_SEED_WEIGHT
    assert w > 0.0


def test_single_play_lighter_than_liked():
    light = _seed_weight(_row(play_count=1))
    liked = _seed_weight(_row(liked=1, play_count=1))
    # The whole point: a played-once track must weigh far less than a like.
    assert liked >= 3.0
    assert light < liked / 2


def test_play_consistency_saturates():
    one = _seed_weight(_row(play_count=1))
    ten = _seed_weight(_row(play_count=10))
    twenty = _seed_weight(_row(play_count=20))
    # More consistent plays → more weight, but it saturates (overexposure).
    assert ten > one
    assert twenty == ten  # capped at _CONSISTENCY_CAP


def test_replays_outweigh_plain_plays():
    played = _seed_weight(_row(play_count=5))
    replayed = _seed_weight(_row(play_count=5, replay_count=2))
    assert replayed > played
    assert replayed >= _WEAK_SEED_WEIGHT


def test_early_skips_downweight():
    clean = _seed_weight(_row(play_count=10))
    skipped = _seed_weight(_row(play_count=10, early_skip_count=8))
    # Many early skips relative to plays drag the weight down...
    assert skipped < clean
    # ...but never to zero — the track still contributes a little.
    assert skipped > 0.0


def test_liked_is_maximum():
    assert _seed_weight(_row(liked=1)) >= 3.0


# ---------------------------------------------------------------------------
# _weighted_centroid
# ---------------------------------------------------------------------------

def test_weighted_centroid_leans_toward_heavy_seed():
    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    heavy_a = [_Seed(1, a, weight=3.0), _Seed(2, b, weight=0.3)]
    c = _weighted_centroid(heavy_a)
    # Centroid must be closer to the heavily-weighted seed.
    assert float(c @ a) > float(c @ b)
    # And L2-normalised.
    assert abs(float(np.linalg.norm(c)) - 1.0) < 1e-5


def test_weighted_centroid_equal_weights_is_mean_direction():
    a = np.array([1.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0], dtype=np.float32)
    c = _weighted_centroid([_Seed(1, a, 1.0), _Seed(2, b, 1.0)])
    # Symmetric weights → equal projection on both axes.
    assert abs(float(c @ a) - float(c @ b)) < 1e-5


# ---------------------------------------------------------------------------
# _cluster_seeds — weak seeds never spawn regions
# ---------------------------------------------------------------------------

def test_weak_seed_does_not_create_region():
    strong = _Seed(1, np.array([1.0, 0.0], dtype=np.float32), weight=3.0)
    # Orthogonal to the strong seed → cannot join its region, and too weak to
    # start its own. It must be dropped, not become a spurious region.
    weak_far = _Seed(2, np.array([0.0, 1.0], dtype=np.float32), weight=0.4)
    regions = _cluster_seeds([strong, weak_far], threshold=0.72, max_regions=30)
    assert len(regions) == 1
    assert {s.track_id for s in regions[0].seeds} == {1}


def test_weak_seed_joins_nearby_region():
    strong = _Seed(1, np.array([1.0, 0.0], dtype=np.float32), weight=3.0)
    # Almost collinear with the strong seed → joins its region and refines it.
    weak_near = _Seed(2, np.array([0.97, 0.05], dtype=np.float32), weight=0.4)
    regions = _cluster_seeds([strong, weak_near], threshold=0.72, max_regions=30)
    assert len(regions) == 1
    assert {s.track_id for s in regions[0].seeds} == {1, 2}


def test_strong_seed_creates_second_region():
    a = _Seed(1, np.array([1.0, 0.0], dtype=np.float32), weight=3.0)
    b = _Seed(2, np.array([0.0, 1.0], dtype=np.float32), weight=2.5)
    regions = _cluster_seeds([a, b], threshold=0.72, max_regions=30)
    assert len(regions) == 2
