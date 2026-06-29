"""Service: score and rank releases for recommendation (Phase 8, Slice 2).

Score formula (from spec):
  score =
    centroid_to_taste_score
    + best_track_evidence
    + region_coverage_score        (simplified: cosine of centroid vs taste centroid)
    + user_release_preference_score
    + freshness_or_forgotten_bonus
    - recently_played_penalty
    - too_many_skipped_tracks_penalty
    - length_bias_penalty
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from app.models import ReleaseAggregate, UserReleasePreference, utc_now

if TYPE_CHECKING:
    from app.store import Store

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@dataclass
class ReleaseRecommendationSettings:
    # Weights (Albums For You mode — personal taste dominant)
    centroid_weight: float = 0.60
    best_track_weight: float = 0.25
    user_pref_weight: float = 0.15
    # Evidence thresholds
    min_matching_tracks: int = 2     # require ≥2 positive tracks (except 1-track releases)
    best_track_threshold: float = 0.72  # cosine similarity to taste centroid
    # Timing windows (ISO-format string comparison via lexicographic order works for UTC)
    recently_played_days: int = 14
    forgotten_days: int = 90         # "long time no listen" bonus threshold
    max_duration_minutes: float = 90.0
    max_releases_per_artist: int = 3
    include_unknown_type: bool = True
    # Score for unplayed but strongly matching releases
    freshness_bonus: float = 0.08
    forgotten_bonus: float = 0.06
    # Penalty magnitudes
    recent_play_penalty: float = 0.20
    high_skip_penalty: float = 0.15
    length_bias_penalty: float = 0.05


def release_recommendation_defaults() -> dict[str, object]:
    s = ReleaseRecommendationSettings()
    return {
        "centroid_weight": s.centroid_weight,
        "best_track_weight": s.best_track_weight,
        "user_pref_weight": s.user_pref_weight,
        "min_matching_tracks": s.min_matching_tracks,
        "best_track_threshold": s.best_track_threshold,
        "recently_played_days": s.recently_played_days,
        "forgotten_days": s.forgotten_days,
        "max_duration_minutes": s.max_duration_minutes,
        "max_releases_per_artist": s.max_releases_per_artist,
        "include_unknown_type": s.include_unknown_type,
        "freshness_bonus": s.freshness_bonus,
        "forgotten_bonus": s.forgotten_bonus,
        "recent_play_penalty": s.recent_play_penalty,
        "high_skip_penalty": s.high_skip_penalty,
        "length_bias_penalty": s.length_bias_penalty,
    }


# ---------------------------------------------------------------------------
# Per-release score
# ---------------------------------------------------------------------------

@dataclass
class ReleaseScore:
    release_id: int
    score: float
    centroid_to_taste: float
    best_track_evidence: float
    user_pref_score: float
    freshness_bonus: float
    recently_played_penalty: float
    high_skip_penalty: float
    length_bias_penalty: float
    positive_track_count: int
    reasons: list[str] = field(default_factory=list)
    debug: dict[str, object] = field(default_factory=dict)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _days_since(iso_timestamp: str | None, now_prefix: str) -> float | None:
    """Rough days since a UTC ISO timestamp using lexicographic comparison."""
    if iso_timestamp is None:
        return None
    # Both are UTC ISO strings — we only need an approximation, so parse year/month/day
    try:
        from datetime import datetime
        try:
            from datetime import UTC
            now = datetime.now(UTC)
        except ImportError:
            from datetime import timezone
            now = datetime.now(timezone.utc)
        played = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        return (now - played).total_seconds() / 86400
    except Exception:
        return None


def score_release(
    agg: ReleaseAggregate,
    release_pref: UserReleasePreference | None,
    track_pref_rows: list[object],
    centroid: np.ndarray | None,
    taste_centroid: np.ndarray | None,
    taste_vectors: list[np.ndarray],
    settings: ReleaseRecommendationSettings,
) -> ReleaseScore:
    """Compute recommendation score for one release.

    Args:
        agg: precomputed release aggregate (centroid_model, medoid, etc.)
        release_pref: user's release-level preference row (may be None = never played)
        track_pref_rows: raw rows from list_release_track_preferences()
        centroid: normalised centroid vector for this release (may be None)
        taste_centroid: normalised centroid of user's positive tracks (may be None)
        taste_vectors: list of individual positive-track embedding vectors
        settings: scoring weights and thresholds
    """
    now_str = utc_now()

    # ------------------------------------------------------------------
    # 1. Centroid-to-taste score
    # ------------------------------------------------------------------
    centroid_to_taste = 0.0
    if centroid is not None and taste_centroid is not None:
        centroid_to_taste = max(0.0, _cosine(centroid, taste_centroid))

    # ------------------------------------------------------------------
    # 2. Best-track evidence: fraction of release tracks matching taste
    # ------------------------------------------------------------------
    total_tracks = max(agg.available_track_count, 1)
    matching_tracks = 0
    positive_track_count = 0
    total_skips = 0
    total_early_skips = 0
    total_plays = 0
    total_completions = 0

    for row in track_pref_rows:
        liked = bool(row["liked"]) if row["liked"] is not None else False
        score_val = float(row["score"]) if row["score"] is not None else 0.0
        skip_count = int(row["skip_count"]) if row["skip_count"] is not None else 0
        early_skips = int(row["early_skip_count"]) if row["early_skip_count"] is not None else 0
        play_count = int(row["play_count"]) if row["play_count"] is not None else 0
        completion_count = int(row["completion_count"]) if row["completion_count"] is not None else 0

        total_skips += skip_count
        total_early_skips += early_skips
        total_plays += play_count
        total_completions += completion_count

        if liked or score_val >= 1.0:
            positive_track_count += 1

    # Count tracks whose centroid similarity exceeds threshold
    if centroid is not None and taste_vectors:
        sims = [_cosine(centroid, tv) for tv in taste_vectors]
        best_sim = max(sims) if sims else 0.0
        # Approximate: distribute best_sim across matching tracks
        # (full per-track similarity requires loading all vectors — deferred to Slice 3 detail view)
        if best_sim >= settings.best_track_threshold:
            matching_tracks = max(positive_track_count, 1)
    else:
        matching_tracks = positive_track_count

    # Require min_matching_tracks (except 1-track releases)
    if total_tracks == 1:
        evidence_ok = matching_tracks >= 1
    else:
        evidence_ok = matching_tracks >= settings.min_matching_tracks

    best_track_evidence = 0.0
    if evidence_ok and total_tracks > 0:
        best_track_evidence = min(1.0, matching_tracks / total_tracks)

    # ------------------------------------------------------------------
    # 3. User release preference score
    # ------------------------------------------------------------------
    user_pref_score = 0.0
    if release_pref is not None:
        raw = float(release_pref.score)
        if release_pref.liked:
            raw += 5.0
        # Normalise to [0, 1] range using soft sigmoid-like scaling
        user_pref_score = min(1.0, max(0.0, raw / 10.0))

    # ------------------------------------------------------------------
    # 4. Freshness / forgotten bonus
    # ------------------------------------------------------------------
    freshness_bonus = 0.0
    if release_pref is None or release_pref.last_played_at is None:
        freshness_bonus = settings.freshness_bonus
    else:
        days = _days_since(release_pref.last_played_at, now_str)
        if days is not None and days >= settings.forgotten_days:
            freshness_bonus = settings.forgotten_bonus

    # ------------------------------------------------------------------
    # 5. Recently played penalty
    # ------------------------------------------------------------------
    recent_penalty = 0.0
    if release_pref is not None and release_pref.last_played_at is not None:
        days = _days_since(release_pref.last_played_at, now_str)
        if days is not None and days < settings.recently_played_days:
            fade = 1.0 - (days / settings.recently_played_days)
            recent_penalty = settings.recent_play_penalty * fade

    # ------------------------------------------------------------------
    # 6. High-skip penalty
    # ------------------------------------------------------------------
    skip_penalty = 0.0
    if total_plays > 0:
        skip_rate = total_early_skips / total_plays
        if skip_rate > 0.5:
            skip_penalty = settings.high_skip_penalty * min(1.0, (skip_rate - 0.5) * 2)

    # ------------------------------------------------------------------
    # 7. Length bias penalty (very long releases)
    # ------------------------------------------------------------------
    length_penalty = 0.0
    if agg.duration is not None:
        duration_min = agg.duration / 60.0
        if duration_min > settings.max_duration_minutes:
            length_penalty = settings.length_bias_penalty

    # ------------------------------------------------------------------
    # Final score
    # ------------------------------------------------------------------
    score = (
        settings.centroid_weight * centroid_to_taste
        + settings.best_track_weight * best_track_evidence
        + settings.user_pref_weight * user_pref_score
        + freshness_bonus
        - recent_penalty
        - skip_penalty
        - length_penalty
    )
    score = max(0.0, score)

    # ------------------------------------------------------------------
    # Reason strings
    # ------------------------------------------------------------------
    reasons: list[str] = []
    if positive_track_count >= 3:
        reasons.append(f"You liked {positive_track_count} tracks")
    elif positive_track_count >= 1:
        reasons.append(f"You liked {positive_track_count} track")
    if centroid_to_taste >= 0.8:
        reasons.append("Fits your taste closely")
    elif centroid_to_taste >= 0.65:
        reasons.append("Near your taste region")
    if freshness_bonus == settings.forgotten_bonus and freshness_bonus > 0:
        reasons.append("Long time since last played")
    elif freshness_bonus == settings.freshness_bonus and freshness_bonus > 0:
        reasons.append("Not yet played")
    if total_completions >= 3:
        reasons.append("Several tracks completed")

    return ReleaseScore(
        release_id=agg.release_id,
        score=score,
        centroid_to_taste=centroid_to_taste,
        best_track_evidence=best_track_evidence,
        user_pref_score=user_pref_score,
        freshness_bonus=freshness_bonus,
        recently_played_penalty=recent_penalty,
        high_skip_penalty=skip_penalty,
        length_bias_penalty=length_penalty,
        positive_track_count=positive_track_count,
        reasons=reasons,
        debug={
            "matching_tracks": matching_tracks,
            "total_tracks": agg.available_track_count,
            "evidence_ok": evidence_ok,
            "total_skips": total_skips,
            "total_early_skips": total_early_skips,
            "total_plays": total_plays,
        },
    )


# ---------------------------------------------------------------------------
# Batch ranking
# ---------------------------------------------------------------------------

@dataclass
class RankedRelease:
    release_id: int
    score: float
    reasons: list[str]
    medoid_track_id: int | None
    score_breakdown: dict[str, object]


def rank_releases_for_user(
    store: Store,
    model_name: str,
    *,
    limit: int = 20,
    settings: ReleaseRecommendationSettings | None = None,
    exclude_release_ids: set[int] | None = None,
    source_centroid: np.ndarray | None = None,
    source_weight: float = 0.0,
) -> list[RankedRelease]:
    """Rank all releases with ready aggregates for a user.

    Args:
        source_centroid: if provided, blends source-release similarity into taste score
            (used by release-page recommendations with source_weight > 0)
        source_weight: weight of source centroid vs personal taste centroid (0.0 = pure personal)
    """
    if settings is None:
        settings = ReleaseRecommendationSettings()

    exclude_ids = exclude_release_ids or set()

    # Load all ready aggregates
    aggregates = store.list_ready_release_aggregates(model_name)
    if not aggregates:
        return []

    # Build taste profile from positive tracks
    positive_track_ids = store.list_positive_track_ids_with_embeddings(
        model_name, min_score=1.0, limit=500
    )
    taste_vectors: list[np.ndarray] = []
    for tid in positive_track_ids:
        v = store.load_embedding(tid, model_name)
        if v is not None:
            taste_vectors.append(v)

    taste_centroid: np.ndarray | None = None
    if taste_vectors:
        tc = np.mean(taste_vectors, axis=0).astype(np.float32)
        norm = float(np.linalg.norm(tc))
        if norm > 0:
            taste_centroid = tc / norm

    # Effective taste centroid: blend with source if provided
    if source_centroid is not None and taste_centroid is not None and source_weight > 0.0:
        personal_weight = 1.0 - source_weight
        blended = source_weight * source_centroid + personal_weight * taste_centroid
        norm = float(np.linalg.norm(blended))
        effective_centroid = blended / norm if norm > 0 else taste_centroid
    else:
        effective_centroid = taste_centroid

    results: list[RankedRelease] = []
    artist_counts: dict[int, int] = {}

    for agg in aggregates:
        if agg.release_id in exclude_ids:
            continue

        centroid = store.load_release_embedding(agg.release_id, model_name)
        release_pref = store.get_release_preference(agg.release_id)
        track_pref_rows = store.list_release_track_preferences(agg.release_id)

        rs = score_release(
            agg=agg,
            release_pref=release_pref,
            track_pref_rows=track_pref_rows,
            centroid=centroid,
            taste_centroid=effective_centroid,
            taste_vectors=taste_vectors,
            settings=settings,
        )

        results.append(
            RankedRelease(
                release_id=agg.release_id,
                score=rs.score,
                reasons=rs.reasons,
                medoid_track_id=agg.medoid_track_id,
                score_breakdown={
                    "centroid_to_taste": rs.centroid_to_taste,
                    "best_track_evidence": rs.best_track_evidence,
                    "user_pref_score": rs.user_pref_score,
                    "freshness_bonus": rs.freshness_bonus,
                    "recently_played_penalty": rs.recently_played_penalty,
                    "high_skip_penalty": rs.high_skip_penalty,
                    "length_bias_penalty": rs.length_bias_penalty,
                    "positive_track_count": rs.positive_track_count,
                    **rs.debug,
                },
            )
        )

    results.sort(key=lambda r: r.score, reverse=True)

    # Apply per-artist cap
    capped: list[RankedRelease] = []
    for r in results:
        artists = store.artist_ids_for_release(r.release_id)
        if any(artist_counts.get(aid, 0) >= settings.max_releases_per_artist for aid in artists):
            continue
        for aid in artists:
            artist_counts[aid] = artist_counts.get(aid, 0) + 1
        capped.append(r)
        if len(capped) >= limit:
            break

    return capped
