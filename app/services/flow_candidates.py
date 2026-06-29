"""Candidate pool builder and reranker for the Flow engine (Phase 9, Slice 3).

Score formula (per plan):
  final_score =
    long_term_region_fit
    + short_term_session_fit
    + track_preference_score
    + novelty_or_familiarity_term
    + continuity_term
    - session_skip_penalty
    - fatigue_penalty
    - repetition_penalty

Pipeline:
1. build_candidate_pool() — multiple sources: HNSW centroid (primary),
   representative tracks, recently accepted, high-signal long-term tracks
2. filter_candidates()    — hard filters: played, missing, disliked, recent hard skips
3. score_candidates()     — all score terms above
4. select_tracks()        — top-k with per-artist / per-release diversity caps
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from app.store import Store
    from app.config import Settings
    from app.models import FlowRegion

logger = logging.getLogger(__name__)

# Hard-skip threshold: skip this many times in session → exclude from pool
_SESSION_HARD_SKIP_THRESHOLD = 2

# Weight of track_pref within long-term fit (rest goes to embedding region_fit).
# Low on purpose: region_fit already encodes "in your taste"; track_pref is a nudge.
_TRACK_PREF_WEIGHT = 0.10

# Adaptive exploration: a region with little signal is an uncertain estimate of
# taste, so Flow should explore more; a region built from many seeds is a
# confident estimate, so Flow should exploit. Bandit intuition (Spotify, Deezer):
# exploration ∝ uncertainty. Replaces the fixed 0.10 discovery ratio.
_EXPLORE_CONFIDENT = 0.10     # lots of signal → mostly exploit
_EXPLORE_UNCERTAIN = 0.35     # little signal → explore aggressively
_SEED_CONFIDENCE_FULL = 20    # seed count at which taste is "well established"

# Session negative feedback: candidates near the centroid of skipped tracks are
# penalised. Skips ≈ dislike but noisy (Deezer), so this is a moderate nudge,
# not a ban — only the part of the similarity above the threshold counts.
_NEGATIVE_SIM_THRESHOLD = 0.50
_NEGATIVE_PENALTY_STRENGTH = 0.30


def adaptive_exploration_level(
    seed_count: int,
    *,
    confident: float = _EXPLORE_CONFIDENT,
    uncertain: float = _EXPLORE_UNCERTAIN,
    full: int = _SEED_CONFIDENCE_FULL,
) -> float:
    """Initial discovery ratio from how much signal a region carries.

    seed_count == 0  → fully uncertain → `uncertain`
    seed_count >= full → fully confident → `confident`
    linear in between. Per-session feedback then nudges this up/down.
    """
    confidence = min(1.0, max(0, seed_count) / max(1, full))
    return round(uncertain - confidence * (uncertain - confident), 4)


# ---------------------------------------------------------------------------
# Session context
# ---------------------------------------------------------------------------

@dataclass
class FlowSessionContext:
    """Runtime state for one Flow session — rebuilt on each refill."""
    session_id: str
    region_id: str
    model_key: str = "discogs_multi"

    played_track_ids: set[int] = field(default_factory=set)
    current_track_id: int | None = None

    # int keys — converted from state_json strings on restore
    session_skipped: dict[int, int] = field(default_factory=dict)
    session_accepted: dict[int, int] = field(default_factory=dict)
    session_artist_plays: dict[int, int] = field(default_factory=dict)
    session_release_plays: dict[int, int] = field(default_factory=dict)

    exploration_level: float = 0.10

    # Embeddings of recently accepted tracks — loaded on context restore
    # Used for continuity_term without per-candidate embedding reads
    recent_accepted_vectors: list[np.ndarray] = field(default_factory=list)

    # Embeddings of tracks skipped this session — loaded on context restore.
    # Used to build a session "negative centroid": Flow steers candidates away
    # from the direction the user has been skipping (inference-time analog of
    # contrastive negative feedback).
    recent_skipped_vectors: list[np.ndarray] = field(default_factory=list)

    # Exclude tracks played within this many days from the candidate pool, so a
    # fresh Flow session does not replay what the user heard recently across
    # *previous* sessions. 0 disables cross-session exclusion.
    exclude_played_days: int = 0


# ---------------------------------------------------------------------------
# Scoring result
# ---------------------------------------------------------------------------

@dataclass
class ScoredCandidate:
    track_id: int
    artist_id: int | None
    release_id: int | None
    final_score: float
    region_fit: float
    track_pref_score: float
    novelty_familiarity: float
    continuity_score: float
    session_skip_penalty: float
    fatigue_penalty: float
    repetition_penalty: float
    negative_penalty: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def load_embeddings_batch(
    store: Store, track_ids: list[int], model_key: str
) -> dict[int, np.ndarray]:
    """Load embeddings for multiple tracks in one SQL query."""
    if not track_ids:
        return {}
    placeholders = ",".join("?" * len(track_ids))
    with store.connect() as conn:  # type: ignore[attr-defined]
        rows = conn.execute(
            f"SELECT track_id, dim, vector FROM embeddings"
            f" WHERE track_id IN ({placeholders}) AND model_name = ?",
            (*track_ids, model_key),
        ).fetchall()
    return {
        int(r["track_id"]): np.frombuffer(
            r["vector"], dtype=np.float32, count=int(r["dim"])
        ).copy()
        for r in rows
    }


def _recent_accepted_centroid(session: FlowSessionContext) -> np.ndarray | None:
    """Centroid of recent_accepted_vectors already loaded into context."""
    if not session.recent_accepted_vectors:
        return None
    vecs = np.vstack(session.recent_accepted_vectors).astype(np.float32)
    c = vecs.mean(axis=0)
    n = float(np.linalg.norm(c))
    return c / n if n > 0 else c


def _negative_centroid(session: FlowSessionContext) -> np.ndarray | None:
    """L2-normalised centroid of tracks skipped this session, or None."""
    if not session.recent_skipped_vectors:
        return None
    vecs = np.vstack(session.recent_skipped_vectors).astype(np.float32)
    c = vecs.mean(axis=0)
    n = float(np.linalg.norm(c))
    return c / n if n > 0 else c


# ---------------------------------------------------------------------------
# Pool builder — multiple candidate sources
# ---------------------------------------------------------------------------

@dataclass
class PoolDiagnostics:
    centroid_loaded: bool = False
    hnsw_count: int = 0
    hnsw_error: str | None = None
    seeds_excluded: int = 0       # seed tracks kept OUT of the pool
    recently_played_excluded: int = 0  # cross-session recently-played excluded
    accepted_added: int = 0
    fallback_used: bool = False
    total: int = 0


def build_candidate_pool(
    store: Store,
    app_settings: Settings,
    region: FlowRegion,
    session: FlowSessionContext,
    pool_size: int = 1000,
) -> tuple[list[tuple[int, float]], PoolDiagnostics, dict[int, str]]:
    """Collect Flow candidates: HNSW neighbours of the region centroid.

    Seeds (the liked/high-signal tracks that *define* the region) are
    deliberately excluded from the candidate pool — Flow plays neighbours in
    embedding space, not the tracks the user already knows and rated. Replaying
    seeds is what made early Flow output identical to the user's own likes.

    Sources:
    1. HNSW around active region centroid — the only generative source
    2. Recently accepted tracks this session — reinforce current direction

    Returns:
        pool       — list of (track_id, similarity)
        diag       — per-source counts and error info
        source_map — track_id → source label (for debug breakdown)
    """
    diag = PoolDiagnostics()
    source_map: dict[int, str] = {}

    centroid = store.load_flow_region_embedding(region.id, session.model_key)
    diag.centroid_loaded = centroid is not None

    exclude = set(session.played_track_ids)
    if session.current_track_id is not None:
        exclude.add(session.current_track_id)
    # Cross-session: drop tracks heard within the recent window so Flow stops
    # serving the same already-played tracks on every restart.
    if session.exclude_played_days > 0:
        recent_played = store.recently_played_track_ids(session.exclude_played_days)
        diag.recently_played_excluded = len(recent_played)
        exclude |= recent_played
    hard_skipped = {
        tid for tid, cnt in session.session_skipped.items()
        if cnt >= _SESSION_HARD_SKIP_THRESHOLD
    }
    exclude |= hard_skipped

    # Exclude the region's own seeds — they built the centroid, don't replay them.
    seed_tracks = store.list_flow_region_tracks(region.id, role="seed")
    seed_ids = {rt.track_id for rt in seed_tracks}
    diag.seeds_excluded = len(seed_ids)
    exclude |= seed_ids

    pool: dict[int, float] = {}  # track_id → similarity

    # --- Source 1: HNSW centroid (only generative source) ---
    if centroid is not None:
        try:
            from app.recommender import Recommender, StaleIndexError
            rec = Recommender(store, app_settings, session.model_key)
            results = rec.similar_vector(
                centroid,
                exclude_track_ids=exclude,
                album_seeds=[],
                k=pool_size,
                max_per_artist=pool_size,
                exclude_same_album=False,
                candidate_multiplier=3,
            )
            for r in results:
                pool[r.track.id] = r.similarity
                source_map[r.track.id] = "hnsw"
            diag.hnsw_count = len(results)
            logger.debug("HNSW source: %d tracks region=%s", len(results), region.id)
        except (FileNotFoundError, StaleIndexError) as exc:
            diag.hnsw_error = str(exc)
            logger.warning("HNSW unavailable: %s", exc)
        except Exception as exc:
            diag.hnsw_error = f"unexpected: {exc}"
            logger.exception("Unexpected error in HNSW pool query")

    # --- Source 2: Recently accepted tracks (session reinforcement) ---
    before = len(pool)
    for tid in list(session.session_accepted.keys())[:20]:
        if tid not in exclude and tid not in pool:
            pool[tid] = 0.80
            source_map[tid] = "session_accepted"
    diag.accepted_added = len(pool) - before

    if not pool:
        # Emergency degradation only: HNSW unavailable AND no session signal.
        # Better to replay seeds than to hand the user a dead Flow.
        logger.warning("All pool sources empty — emergency fallback to region seeds")
        diag.fallback_used = True
        played = session.played_track_ids
        result = [(rt.track_id, 0.50) for rt in seed_tracks[:pool_size] if rt.track_id not in played]
        for tid, _ in result:
            source_map[tid] = "fallback_seed"
        diag.total = len(result)
        return result, diag, source_map

    diag.total = len(pool)
    return list(pool.items()), diag, source_map


# ---------------------------------------------------------------------------
# Filter — hard exclusions only
# ---------------------------------------------------------------------------

def filter_candidates(
    pool: list[tuple[int, float]],
    store: Store,
    session: FlowSessionContext,
) -> list[tuple[int, float, int | None, int | None]]:
    """Return (track_id, similarity, artist_id, release_id) passing hard filters.

    Hard filters (per plan):
    - already played / current track
    - missing/lost tracks
    - explicitly disliked
    - recent hard skips (>= threshold session skips)
    """
    if not pool:
        return []

    hard_skipped = {
        tid for tid, cnt in session.session_skipped.items()
        if cnt >= _SESSION_HARD_SKIP_THRESHOLD
    }
    played = session.played_track_ids
    if session.current_track_id is not None:
        played = played | {session.current_track_id}

    track_ids = [tid for tid, _ in pool]
    sim_by_id = {tid: sim for tid, sim in pool}

    placeholders = ",".join("?" * len(track_ids))
    with store.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT
                t.id AS track_id,
                t.missing_at,
                ta.artist_id,
                rt.release_id,
                COALESCE(utp.disliked, 0) AS disliked
            FROM tracks t
            LEFT JOIN track_artists ta ON ta.track_id = t.id
                AND ta.artist_id = (
                    SELECT artist_id FROM track_artists
                    WHERE track_id = t.id ORDER BY position LIMIT 1
                )
            LEFT JOIN release_tracks rt ON rt.track_id = t.id
                AND rt.release_id = (
                    SELECT release_id FROM release_tracks
                    WHERE track_id = t.id ORDER BY rowid LIMIT 1
                )
            LEFT JOIN user_track_preferences utp ON utp.track_id = t.id
            WHERE t.id IN ({placeholders})
            """,
            track_ids,
        ).fetchall()

    row_by_id = {int(r["track_id"]): r for r in rows}
    result = []
    for tid, _ in pool:
        if tid in played:
            continue
        if tid in hard_skipped:
            continue
        row = row_by_id.get(tid)
        if row is None:
            continue
        if row["missing_at"] is not None:
            continue
        if bool(row["disliked"]):
            continue
        artist_id = int(row["artist_id"]) if row["artist_id"] is not None else None
        release_id = int(row["release_id"]) if row["release_id"] is not None else None
        result.append((tid, sim_by_id[tid], artist_id, release_id))

    return result


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------

def score_candidates(
    candidates: list[tuple[int, float, int | None, int | None]],
    store: Store,
    session: FlowSessionContext,
    *,
    long_term_weight: float = 0.70,
    session_weight: float = 0.30,
    skip_penalty_strength: float = 0.50,
) -> list[ScoredCandidate]:
    """Score per plan formula:

    final_score =
      long_term_region_fit
      + short_term_session_fit
      + track_preference_score
      + novelty_or_familiarity_term
      + continuity_term
      - session_skip_penalty
      - fatigue_penalty
      - repetition_penalty
    """
    if not candidates:
        return []

    track_ids = [tid for tid, _, _, _ in candidates]
    placeholders = ",".join("?" * len(track_ids))
    with store.connect() as conn:
        pref_rows = conn.execute(
            f"""
            SELECT track_id, liked, score, skip_count, early_skip_count,
                   play_count, completion_count, replay_count
            FROM user_track_preferences
            WHERE track_id IN ({placeholders})
            """,
            track_ids,
        ).fetchall()
    pref_map = {int(r["track_id"]): r for r in pref_rows}

    # Continuity centroid from pre-loaded accepted vectors (no per-candidate loads)
    recent_centroid = _recent_accepted_centroid(session)
    # Negative centroid from skipped tracks — steer candidates away from it.
    neg_centroid = _negative_centroid(session)

    # Batch-load candidate embeddings only when a vector term is active
    # (continuity and/or negative penalty both need per-candidate vectors).
    candidate_vecs: dict[int, np.ndarray] = {}
    if recent_centroid is not None or neg_centroid is not None:
        candidate_vecs = load_embeddings_batch(store, track_ids, session.model_key)

    scored: list[ScoredCandidate] = []
    for tid, hnsw_sim, artist_id, release_id in candidates:

        # long_term_region_fit — cosine from HNSW, no extra DB read
        region_fit = max(0.0, float(hnsw_sim))

        # track_preference_score
        pref = pref_map.get(tid)
        track_pref = 0.5  # neutral for unplayed tracks
        if pref is not None:
            raw = float(pref["score"] or 0)
            if bool(pref["liked"]):
                raw = max(raw, 2.0)
            track_pref = max(0.0, min(1.0, (raw + 3.0) / 6.0))

        # novelty_or_familiarity_term
        # familiar = liked/completed many times → small positive reinforcement
        # novel = never played → exploration bonus scaled by exploration_level
        novelty_familiarity = 0.0
        if pref is None or int(pref["play_count"] or 0) == 0:
            # novel track
            novelty_familiarity = session.exploration_level * 0.15
        else:
            play_count = int(pref["play_count"] or 0)
            completion_count = int(pref["completion_count"] or 0)
            if play_count > 0 and (bool(pref["liked"]) or completion_count / play_count >= 0.7):
                # familiar beloved track
                novelty_familiarity = (1.0 - session.exploration_level) * 0.10

        # continuity_term — cosine to centroid of recently accepted tracks
        continuity = 0.0
        if recent_centroid is not None:
            vec = candidate_vecs.get(tid)
            if vec is not None:
                continuity = max(0.0, _cosine(vec, recent_centroid)) * 0.20

        # negative_penalty — proximity to the centroid of skipped tracks. Only
        # the similarity above the threshold counts, so neutral candidates are
        # untouched and only those genuinely close to "skip territory" suffer.
        negative_penalty = 0.0
        if neg_centroid is not None:
            vec = candidate_vecs.get(tid)
            if vec is not None:
                neg_sim = _cosine(vec, neg_centroid)
                if neg_sim > _NEGATIVE_SIM_THRESHOLD:
                    negative_penalty = (
                        (neg_sim - _NEGATIVE_SIM_THRESHOLD)
                        / (1.0 - _NEGATIVE_SIM_THRESHOLD)
                        * _NEGATIVE_PENALTY_STRENGTH
                    )

        # short_term_session_fit
        session_fit = min(0.15, session.session_accepted.get(tid, 0) * 0.05)

        # session_skip_penalty
        skip_ct = session.session_skipped.get(tid, 0)
        session_penalty = min(0.80, skip_ct * skip_penalty_strength) if skip_ct > 0 else 0.0

        # fatigue_penalty — long-term high skip ratio
        fatigue_penalty = 0.0
        if pref is not None:
            play_count = int(pref["play_count"] or 0)
            if play_count > 3:
                skip_ratio = (
                    int(pref["skip_count"] or 0) + int(pref["early_skip_count"] or 0)
                ) / play_count
                fatigue_penalty = min(0.30, skip_ratio * 0.5)

        # repetition_penalty — artist/release played too many times this session
        repetition_penalty = 0.0
        if artist_id is not None:
            artist_plays = session.session_artist_plays.get(artist_id, 0)
            if artist_plays >= 3:
                repetition_penalty += min(0.20, (artist_plays - 2) * 0.07)
        if release_id is not None:
            release_plays = session.session_release_plays.get(release_id, 0)
            if release_plays >= 2:
                repetition_penalty += min(0.15, (release_plays - 1) * 0.08)
        repetition_penalty = min(0.30, repetition_penalty)

        # Embedding proximity (region_fit) is the primary taste signal; track_pref
        # is only a minor nudge. Earlier it carried 0.30 of long-term fit, which —
        # combined with seeds in the pool — made Flow replay the user's own likes.
        long_term = (1.0 - _TRACK_PREF_WEIGHT) * region_fit + _TRACK_PREF_WEIGHT * track_pref
        base = long_term_weight * long_term + session_weight * session_fit
        final = max(0.0, min(1.0,
            base
            + novelty_familiarity
            + continuity
            - session_penalty
            - fatigue_penalty
            - repetition_penalty
            - negative_penalty
        ))

        scored.append(ScoredCandidate(
            track_id=tid,
            artist_id=artist_id,
            release_id=release_id,
            final_score=final,
            region_fit=region_fit,
            track_pref_score=track_pref,
            novelty_familiarity=novelty_familiarity,
            continuity_score=continuity,
            session_skip_penalty=session_penalty,
            fatigue_penalty=fatigue_penalty,
            repetition_penalty=repetition_penalty,
            negative_penalty=negative_penalty,
            score_breakdown={
                "region_fit": round(region_fit, 4),
                "track_pref": round(track_pref, 4),
                "novelty_familiarity": round(novelty_familiarity, 4),
                "continuity": round(continuity, 4),
                "session_fit": round(session_fit, 4),
                "session_penalty": round(session_penalty, 4),
                "fatigue_penalty": round(fatigue_penalty, 4),
                "repetition_penalty": round(repetition_penalty, 4),
                "negative_penalty": round(negative_penalty, 4),
                "final": round(final, 4),
            },
        ))

    scored.sort(key=lambda c: c.final_score, reverse=True)
    return scored


# ---------------------------------------------------------------------------
# Selection with diversity caps + exploration slots
# ---------------------------------------------------------------------------

def select_tracks(
    scored: list[ScoredCandidate],
    *,
    n: int = 5,
    max_per_artist: int = 2,
    max_per_release: int = 1,
    exploration_ratio: float = 0.10,
) -> list[ScoredCandidate]:
    """Pick top-n with artist/release diversity caps.

    Exploration slots filled from the lower half of the scored list (less
    obvious / more novel tracks). round(5 * 0.10) == 0 under banker's rounding,
    so guarantee at least one exploration slot whenever the ratio is positive
    and the queue is large enough — otherwise discovery never fires at defaults.
    """
    n_explore = round(n * exploration_ratio)
    if exploration_ratio > 0 and n_explore == 0 and n >= 3:
        n_explore = 1
    n_explore = min(n_explore, n)
    n_main = n - n_explore

    artist_seen: dict[int | None, int] = {}
    release_seen: dict[int | None, int] = {}
    selected: list[ScoredCandidate] = []

    def _can_add(c: ScoredCandidate) -> bool:
        if artist_seen.get(c.artist_id, 0) >= max_per_artist:
            return False
        if c.release_id is not None and release_seen.get(c.release_id, 0) >= max_per_release:
            return False
        return True

    def _record(c: ScoredCandidate) -> None:
        artist_seen[c.artist_id] = artist_seen.get(c.artist_id, 0) + 1
        release_seen[c.release_id] = release_seen.get(c.release_id, 0) + 1
        selected.append(c)

    for c in scored:
        if len(selected) >= n_main:
            break
        if _can_add(c):
            _record(c)

    if n_explore > 0:
        lower_half = scored[len(scored) // 2:]
        used = {c.track_id for c in selected}
        for c in lower_half:
            if len(selected) >= n:
                break
            if c.track_id in used:
                continue
            if _can_add(c):
                _record(c)

    return selected


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def fill_flow_queue(
    store: Store,
    app_settings: Settings,
    region: FlowRegion,
    session: FlowSessionContext,
    *,
    n: int = 5,
    pool_size: int = 1000,
    max_per_artist: int = 2,
    max_per_release: int = 1,
    long_term_weight: float = 0.70,
    session_weight: float = 0.30,
    skip_penalty_strength: float = 0.50,
) -> tuple[list[ScoredCandidate], dict[str, object]]:
    """Full pipeline: pool → filter → score → select."""
    raw_pool, diag, source_map = build_candidate_pool(store, app_settings, region, session, pool_size=pool_size)
    filtered = filter_candidates(raw_pool, store, session)
    scored = score_candidates(
        filtered, store, session,
        long_term_weight=long_term_weight,
        session_weight=session_weight,
        skip_penalty_strength=skip_penalty_strength,
    )
    selected = select_tracks(
        scored, n=n,
        max_per_artist=max_per_artist,
        max_per_release=max_per_release,
        exploration_ratio=session.exploration_level,
    )

    # Inject source into score_breakdown for each selected track
    for c in selected:
        c.score_breakdown["source"] = source_map.get(c.track_id, "unknown")

    summary = {
        "pool_size": diag.total,
        "pool_sources": {
            "hnsw": diag.hnsw_count,
            "session_accepted": diag.accepted_added,
            "seeds_excluded": diag.seeds_excluded,
            "fallback_used": diag.fallback_used,
            "centroid_loaded": diag.centroid_loaded,
            "hnsw_error": diag.hnsw_error,
        },
        "filtered_size": len(filtered),
        "scored_size": len(scored),
        "selected_count": len(selected),
        "region_id": region.id,
        "exploration_level": session.exploration_level,
        "top_scores": [round(c.final_score, 3) for c in selected],
        "score_ranges": {
            "max": round(scored[0].final_score, 4) if scored else None,
            "min": round(scored[-1].final_score, 4) if scored else None,
            "p50": round(scored[len(scored) // 2].final_score, 4) if scored else None,
        },
    }
    logger.debug("fill_flow_queue summary=%s", summary)
    return selected, summary
