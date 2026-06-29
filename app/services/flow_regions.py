"""Service: build Flow taste regions from positive user signals (Phase 9, Slice 2).

Algorithm: similarity-threshold clustering.

1. Collect positive seeds weighted by signal strength.
2. Sort by weight descending.
3. Greedily assign each unassigned seed to an existing region or start a new one.
4. Compute normalised centroid per region.
5. Find medoid (seed closest to centroid).
6. Pick representative tracks (top-k by similarity to centroid).
7. Estimate candidate coverage via HNSW.
8. Persist profile + regions + region tracks + centroid vectors.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from app.models import FlowRegionTrack, utc_now

if TYPE_CHECKING:
    from app.store import Store
    from app.config import Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@dataclass
class FlowSettings:
    model_key: str = "discogs_multi"
    # Region clustering
    region_threshold: float = 0.72       # cosine similarity for seed assignment
    min_seeds_per_region: int = 1        # preserve small regions
    max_regions: int = 30
    max_representative_tracks: int = 5
    # Seed collection
    min_liked_score: float = 1.0         # user_track_preferences.score threshold
    max_seeds: int = 500
    # Candidate coverage estimation
    coverage_k: int = 200                # HNSW neighbours to query per centroid
    # Visible queue
    visible_buffer: int = 5
    # Candidate pool
    candidate_pool_size: int = 1000
    # Scoring weights (for Slice 3)
    long_term_weight: float = 0.70
    session_weight: float = 0.30
    exploration_ratio: float = 0.10
    max_per_artist: int = 2
    max_per_release: int = 1
    skip_penalty_strength: float = 0.50
    # Region switching
    region_switch_skip_count: int = 2    # early skips in last N plays to trigger switch
    region_switch_window: int = 5


def flow_settings_defaults() -> dict[str, object]:
    s = FlowSettings()
    return {
        "model_key": s.model_key,
        "region_threshold": s.region_threshold,
        "min_seeds_per_region": s.min_seeds_per_region,
        "max_regions": s.max_regions,
        "visible_buffer": s.visible_buffer,
        "candidate_pool_size": s.candidate_pool_size,
        "long_term_weight": s.long_term_weight,
        "session_weight": s.session_weight,
        "exploration_ratio": s.exploration_ratio,
        "max_per_artist": s.max_per_artist,
        "max_per_release": s.max_per_release,
        "skip_penalty_strength": s.skip_penalty_strength,
        "region_switch_skip_count": s.region_switch_skip_count,
        "region_switch_window": s.region_switch_window,
    }


# ---------------------------------------------------------------------------
# Seed collection
# ---------------------------------------------------------------------------

@dataclass
class _Seed:
    track_id: int
    vector: np.ndarray
    weight: float
    region_index: int = -1   # assigned after clustering


def _collect_seeds(store: Store, settings: FlowSettings) -> list[_Seed]:
    """Load positive-signal tracks with embeddings, deduped, sorted by weight."""
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT
                utp.track_id,
                utp.liked,
                utp.replay_count,
                utp.completion_count,
                utp.score,
                utp.play_count,
                utp.last_played_at
            FROM user_track_preferences utp
            JOIN embeddings e ON e.track_id = utp.track_id AND e.model_name = ?
            JOIN tracks t ON t.id = utp.track_id
            WHERE t.missing_at IS NULL
              AND (
                  utp.liked = 1
                  OR utp.replay_count >= 1
                  OR utp.completion_count >= 2
                  OR utp.score >= ?
              )
            ORDER BY
                utp.liked DESC,
                utp.replay_count DESC,
                utp.score DESC,
                utp.completion_count DESC
            LIMIT ?
            """,
            (settings.model_key, settings.min_liked_score, settings.max_seeds),
        ).fetchall()

    seeds: list[_Seed] = []
    seen: set[int] = set()
    for row in rows:
        tid = int(row["track_id"])
        if tid in seen:
            continue
        seen.add(tid)
        vector = store.load_embedding(tid, settings.model_key)
        if vector is None:
            continue
        # Weight assignment
        weight = 1.0
        if bool(row["liked"]):
            weight = max(weight, 3.0)
        if int(row["replay_count"] or 0) >= 1:
            weight = max(weight, 2.5)
        if int(row["completion_count"] or 0) >= 3:
            weight = max(weight, 2.0)
        elif int(row["completion_count"] or 0) >= 2:
            weight = max(weight, 1.5)
        if float(row["score"] or 0) >= 2.0:
            weight = max(weight, 1.5)
        seeds.append(_Seed(track_id=tid, vector=vector, weight=weight))

    seeds.sort(key=lambda s: s.weight, reverse=True)
    return seeds


# ---------------------------------------------------------------------------
# Similarity-threshold clustering
# ---------------------------------------------------------------------------

def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


@dataclass
class _Region:
    index: int
    seeds: list[_Seed] = field(default_factory=list)
    centroid: np.ndarray | None = None
    medoid_track_id: int | None = None
    representative_track_ids: list[int] = field(default_factory=list)
    candidate_count: int = 0


def _cluster_seeds(seeds: list[_Seed], threshold: float, max_regions: int) -> list[_Region]:
    regions: list[_Region] = []
    for seed in seeds:
        assigned = False
        for region in regions:
            assert region.centroid is not None
            if _cosine(seed.vector, region.centroid) >= threshold:
                region.seeds.append(seed)
                seed.region_index = region.index
                # Recompute centroid incrementally
                vecs = np.vstack([s.vector for s in region.seeds]).astype(np.float32)
                c = vecs.mean(axis=0)
                norm = float(np.linalg.norm(c))
                region.centroid = c / norm if norm > 0 else c
                assigned = True
                break
        if not assigned and len(regions) < max_regions:
            r = _Region(index=len(regions))
            r.seeds.append(seed)
            seed.region_index = r.index
            v = seed.vector.copy().astype(np.float32)
            norm = float(np.linalg.norm(v))
            r.centroid = v / norm if norm > 0 else v
            regions.append(r)
    return regions


def _finalize_region(region: _Region, settings: FlowSettings) -> None:
    """Compute medoid and representatives for a region."""
    if not region.seeds or region.centroid is None:
        return
    vecs = np.vstack([s.vector for s in region.seeds]).astype(np.float32)
    sims = vecs @ region.centroid
    medoid_idx = int(np.argmax(sims))
    region.medoid_track_id = region.seeds[medoid_idx].track_id
    # Representatives: seeds closest to centroid (up to max)
    ranked = sorted(range(len(region.seeds)), key=lambda i: sims[i], reverse=True)
    region.representative_track_ids = [
        region.seeds[i].track_id
        for i in ranked[:settings.max_representative_tracks]
    ]


def _estimate_candidate_coverage(
    region: _Region,
    store: Store,
    app_settings: Settings,
    model_key: str,
    coverage_k: int,
) -> int:
    """Query HNSW around region centroid; return count of reachable tracks."""
    if region.centroid is None:
        return 0
    try:
        from app.recommender import Recommender, StaleIndexError
        rec = Recommender(store, app_settings, model_key)
        seed_ids = {s.track_id for s in region.seeds}
        results = rec.similar_vector(
            region.centroid,
            exclude_track_ids=seed_ids,
            album_seeds=[],
            k=coverage_k,
            max_per_artist=coverage_k,
            exclude_same_album=False,
            candidate_multiplier=2,
        )
        return len(results)
    except (FileNotFoundError, StaleIndexError, LookupError) as exc:
        logger.debug("Coverage estimation skipped: %s", exc)
        return 0
    except Exception:
        logger.exception("Unexpected error estimating candidate coverage")
        return 0


# ---------------------------------------------------------------------------
# Profile builder
# ---------------------------------------------------------------------------

def rebuild_flow_profile(
    store: Store,
    app_settings: Settings,
    settings: FlowSettings | None = None,
) -> dict[str, object]:
    """Full profile rebuild: collect seeds → cluster → persist.

    Returns a summary dict.
    """
    if settings is None:
        settings = FlowSettings()

    logger.info("Flow profile rebuild started model=%s", settings.model_key)

    # Mark profile as building
    profile = store.upsert_flow_profile(settings.model_key, "building",
                                         settings_json=json.dumps(flow_settings_defaults()))

    seeds = _collect_seeds(store, settings)
    logger.info("Flow seeds collected: %d (model=%s)", len(seeds), settings.model_key)

    if not seeds:
        store.upsert_flow_profile(settings.model_key, "empty")
        return {"status": "empty", "seed_count": 0, "region_count": 0}

    regions = _cluster_seeds(seeds, settings.region_threshold, settings.max_regions)

    # Filter regions below min_seeds
    regions = [r for r in regions if len(r.seeds) >= settings.min_seeds_per_region]
    logger.info("Flow regions after clustering: %d", len(regions))

    # Finalize each region
    for region in regions:
        _finalize_region(region, settings)
        region.candidate_count = _estimate_candidate_coverage(
            region, store, app_settings, settings.model_key, settings.coverage_k
        )

    # Persist: clear old regions, write new ones
    store.delete_flow_regions_for_profile(profile.id)
    now = utc_now()

    for region in regions:
        assert region.centroid is not None
        total_weight = sum(s.weight for s in region.seeds)
        # Region weight = total seed signal (normalised later across regions)
        db_region = store.upsert_flow_region(
            profile.id,
            region.index,
            medoid_track_id=region.medoid_track_id,
            weight=total_weight,
            seed_count=len(region.seeds),
            candidate_count=region.candidate_count,
            summary_json=json.dumps({
                "representative_track_ids": region.representative_track_ids,
                "top_seed_ids": [s.track_id for s in region.seeds[:5]],
            }),
            quality_json=json.dumps({
                "seed_count": len(region.seeds),
                "candidate_count": region.candidate_count,
                "total_seed_weight": round(total_weight, 3),
            }),
        )

        # Save centroid vector
        store.save_flow_region_embedding(db_region.id, settings.model_key, region.centroid)

        # Save region tracks
        region_tracks: list[FlowRegionTrack] = []
        seed_set = {s.track_id for s in region.seeds}
        for seed in region.seeds:
            sim = _cosine(seed.vector, region.centroid)
            role = "seed"
            region_tracks.append(FlowRegionTrack(
                region_id=db_region.id,
                track_id=seed.track_id,
                role=role,
                weight=seed.weight,
                distance=round(1.0 - sim, 5),
            ))
        for tid in region.representative_track_ids:
            if tid not in seed_set:
                region_tracks.append(FlowRegionTrack(
                    region_id=db_region.id,
                    track_id=tid,
                    role="representative",
                    weight=None,
                    distance=None,
                ))
        store.replace_flow_region_tracks(db_region.id, region_tracks)

    store.upsert_flow_profile(settings.model_key, "ready", last_built_at=now)

    region_count = len(regions)
    summary = {
        "status": "ready",
        "seed_count": len(seeds),
        "region_count": region_count,
        "regions": [
            {
                "index": r.index,
                "seed_count": len(r.seeds),
                "candidate_count": r.candidate_count,
                "medoid_track_id": r.medoid_track_id,
            }
            for r in regions
        ],
    }
    logger.info(
        "Flow profile rebuild done: regions=%d seeds=%d model=%s",
        region_count, len(seeds), settings.model_key,
    )
    return summary
