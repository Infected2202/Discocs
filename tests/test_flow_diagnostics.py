"""Tests for Flow candidate pool diagnostics (pool_sources breakdown)."""
from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.services.flow_candidates import (
    FlowSessionContext,
    PoolDiagnostics,
    ScoredCandidate,
    adaptive_exploration_level,
    build_candidate_pool,
    select_tracks,
)


def _scored(track_id: int, score: float, artist_id: int | None = None,
            release_id: int | None = None) -> ScoredCandidate:
    return ScoredCandidate(
        track_id=track_id, artist_id=artist_id, release_id=release_id,
        final_score=score, region_fit=score, track_pref_score=0.5,
        novelty_familiarity=0.0, continuity_score=0.0, session_skip_penalty=0.0,
        fatigue_penalty=0.0, repetition_penalty=0.0,
        score_breakdown={"final": score},
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


def test_no_centroid_falls_back_to_seeds():
    # No centroid → no HNSW. Seeds are excluded from the pool, so the only
    # remaining option is the emergency fallback, which returns the seeds.
    store = _make_store(centroid=None, seed_track_ids=[10, 20])
    pool, diag, source_map = build_candidate_pool(store, MagicMock(), _make_region(), _session())

    assert diag.centroid_loaded is False
    assert diag.hnsw_count == 0
    assert diag.hnsw_error is None
    assert diag.seeds_excluded == 2
    assert diag.fallback_used is True
    assert {tid for tid, _ in pool} == {10, 20}
    assert source_map[10] == "fallback_seed"


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


def test_seeds_excluded_from_hnsw_query():
    # The core fix: seed tracks must be passed into HNSW's exclude set so Flow
    # never offers back the tracks that defined the region.
    centroid = np.ones(64, dtype=np.float32)
    store = _make_store(centroid=centroid, seed_track_ids=[1, 2, 3])

    fake_instance = MagicMock()
    fake_instance.similar_vector.return_value = []

    with patch("app.recommender.Recommender", return_value=fake_instance):
        _, diag, _ = build_candidate_pool(store, MagicMock(), _make_region(), _session())

    assert diag.seeds_excluded == 3
    call_kwargs = fake_instance.similar_vector.call_args.kwargs
    excluded = call_kwargs["exclude_track_ids"]
    assert {1, 2, 3}.issubset(excluded)


def test_seeds_not_in_pool_when_hnsw_returns_neighbours():
    centroid = np.ones(64, dtype=np.float32)
    store = _make_store(centroid=centroid, seed_track_ids=[1, 2])

    neighbour = MagicMock()
    neighbour.track.id = 500
    neighbour.similarity = 0.88
    fake_instance = MagicMock()
    fake_instance.similar_vector.return_value = [neighbour]

    with patch("app.recommender.Recommender", return_value=fake_instance):
        pool, _, source_map = build_candidate_pool(store, MagicMock(), _make_region(), _session())

    pool_ids = {tid for tid, _ in pool}
    assert pool_ids == {500}
    assert 1 not in pool_ids
    assert 2 not in pool_ids
    assert source_map[500] == "hnsw"


def test_session_accepted_added_as_source():
    centroid = np.ones(64, dtype=np.float32)
    store = _make_store(centroid=centroid, seed_track_ids=[])

    fake_instance = MagicMock()
    fake_instance.similar_vector.return_value = []

    session = _session()
    session.session_accepted = {42: 3}

    with patch("app.recommender.Recommender", return_value=fake_instance):
        pool, diag, source_map = build_candidate_pool(store, MagicMock(), _make_region(), session)

    assert diag.accepted_added == 1
    assert source_map[42] == "session_accepted"


def test_played_tracks_excluded_from_fallback():
    session = _session()
    session.played_track_ids = {10, 20}
    store = _make_store(centroid=None, seed_track_ids=[10, 20, 30])
    pool, diag, source_map = build_candidate_pool(store, MagicMock(), _make_region(), session)

    # No centroid → fallback to seeds, but played seeds must be filtered out.
    pool_ids = {tid for tid, _ in pool}
    assert pool_ids == {30}


def test_diag_total_matches_pool_length():
    store = _make_store(centroid=None, seed_track_ids=[1, 2, 3])
    pool, diag, _ = build_candidate_pool(store, MagicMock(), _make_region(), _session())
    assert diag.total == len(pool)


def test_fallback_empty_when_no_seeds():
    store = _make_store(centroid=None, seed_track_ids=[])
    pool, diag, source_map = build_candidate_pool(store, MagicMock(), _make_region(), _session())

    assert diag.fallback_used is True
    assert pool == []


# ---------------------------------------------------------------------------
# select_tracks — exploration slot fix
# ---------------------------------------------------------------------------

def test_exploration_slot_guaranteed_at_default_ratio():
    # round(5 * 0.10) == 0 under banker's rounding — discovery must still fire.
    # Top 6 by score; the lowest-scored item lives in the lower half and should
    # only be reachable via an exploration slot.
    scored = [_scored(i, 1.0 - i * 0.1, artist_id=i) for i in range(6)]
    selected = select_tracks(scored, n=5, exploration_ratio=0.10)

    assert len(selected) == 5
    # With 1 exploration slot, n_main=4 → tracks 0..3 fill main; one lower-half
    # track (3,4,5 region) fills the explore slot. Track 5 (lowest) is eligible.
    selected_ids = {c.track_id for c in selected}
    # exactly 4 top tracks + 1 from lower half
    assert len({0, 1, 2, 3} & selected_ids) == 4
    assert (4 in selected_ids) or (5 in selected_ids)


def test_no_exploration_when_ratio_zero():
    scored = [_scored(i, 1.0 - i * 0.1, artist_id=i) for i in range(6)]
    selected = select_tracks(scored, n=5, exploration_ratio=0.0)

    # Pure top-5, no lower-half injection.
    assert {c.track_id for c in selected} == {0, 1, 2, 3, 4}


def test_small_queue_no_forced_exploration():
    # n < 3 must not sacrifice a slot to exploration.
    scored = [_scored(i, 1.0 - i * 0.1, artist_id=i) for i in range(6)]
    selected = select_tracks(scored, n=2, exploration_ratio=0.10)
    assert {c.track_id for c in selected} == {0, 1}


# ---------------------------------------------------------------------------
# adaptive_exploration_level — exploration ∝ uncertainty
# ---------------------------------------------------------------------------

def test_exploration_high_for_empty_region():
    # No seeds → maximally uncertain → explore aggressively.
    assert adaptive_exploration_level(0) == pytest.approx(0.35)


def test_exploration_low_for_rich_region():
    # Many seeds → confident → mostly exploit (floor value).
    assert adaptive_exploration_level(20) == pytest.approx(0.10)
    assert adaptive_exploration_level(100) == pytest.approx(0.10)


def test_exploration_monotonic_decreasing():
    # More signal must never increase exploration.
    levels = [adaptive_exploration_level(n) for n in range(0, 25)]
    assert all(b <= a for a, b in zip(levels, levels[1:]))


def test_exploration_midpoint_between_bounds():
    # Halfway to confidence sits between the two bounds.
    mid = adaptive_exploration_level(10)
    assert 0.10 < mid < 0.35
