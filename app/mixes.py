from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np

from app.config import Settings
from app.index import HnswIndex
from app.mix_covers import generate_mix_cover
from app.store import GeneratedMix, Store, Track


DEFAULT_MIX_MODEL = "discogs_multi"
DEFAULT_DASHBOARD_MIXES = 8
DEFAULT_TRACKS_PER_MIX = 100
DEFAULT_REGION_THRESHOLD = 0.82
DEFAULT_MAX_PER_ARTIST = 4
DEFAULT_MAX_PER_RELEASE = 2
DEFAULT_CANDIDATE_POOL = 1200


@dataclass(frozen=True)
class MixSettings:
    model: str = DEFAULT_MIX_MODEL
    count: int = DEFAULT_DASHBOARD_MIXES
    tracks_per_mix: int = DEFAULT_TRACKS_PER_MIX
    update_cadence: str = "daily"
    region_threshold: float = DEFAULT_REGION_THRESHOLD
    max_per_artist: int = DEFAULT_MAX_PER_ARTIST
    max_per_release: int = DEFAULT_MAX_PER_RELEASE
    candidate_pool: int = DEFAULT_CANDIDATE_POOL
    discovery_ratio: float = 0.75
    novelty_weight: float = 0.6
    duplicate_strictness: str = "strict"
    seed_source: str = "listening_history"


@dataclass(frozen=True)
class TasteSeed:
    track: Track
    vector: np.ndarray
    signal_score: float
    signal: dict[str, object]


@dataclass(frozen=True)
class TasteRegion:
    id: str
    centroid: np.ndarray
    seeds: list[TasteSeed]
    representative: TasteSeed
    top_artists: list[str]
    top_releases: list[str]
    diagnostics: dict[str, object]


@dataclass(frozen=True)
class GeneratedMixResult:
    mixes: list[GeneratedMix]
    regions: list[TasteRegion]
    diagnostics: dict[str, object]


@dataclass(frozen=True)
class DashboardMixEnsureResult:
    generated: list[GeneratedMix]
    diagnostics: dict[str, object]


@dataclass(frozen=True)
class DashboardMixGenerationPlan:
    mix_settings: MixSettings
    existing: list[GeneratedMix]
    active: list[GeneratedMix]
    saved_count: int
    expired: list[GeneratedMix]
    diagnostics: dict[str, object]
    should_generate: bool
    generation_count: int
    reason: str


@dataclass(frozen=True)
class CandidateSource:
    index: HnswIndex | None
    ids: np.ndarray
    vectors: np.ndarray
    diagnostics: dict[str, object]


def generated_mix_default_settings() -> dict[str, object]:
    return {
        "mix_dashboard_count": DEFAULT_DASHBOARD_MIXES,
        "mix_tracks_per_mix": DEFAULT_TRACKS_PER_MIX,
        "mix_update_cadence": "daily",
        "mix_region_threshold": DEFAULT_REGION_THRESHOLD,
        "mix_discovery_ratio": 0.75,
        "mix_novelty_weight": 0.6,
        "mix_duplicate_strictness": "strict",
        "mix_max_per_artist": DEFAULT_MAX_PER_ARTIST,
        "mix_max_per_release": DEFAULT_MAX_PER_RELEASE,
        "mix_include_small_regions": True,
        "mix_seed_source": "listening_history",
        "mix_model": DEFAULT_MIX_MODEL,
    }


def resolve_mix_settings(
    values: dict[str, object] | None = None,
    *,
    count: int | None = None,
    tracks_per_mix: int | None = None,
) -> MixSettings:
    data = {**generated_mix_default_settings(), **(values or {})}
    return MixSettings(
        model=str(data.get("model") or data.get("mix_model") or DEFAULT_MIX_MODEL),
        count=_bounded_int(count if count is not None else data.get("count", data.get("mix_dashboard_count")), 8, 1, 20),
        tracks_per_mix=_bounded_int(
            tracks_per_mix if tracks_per_mix is not None else data.get("tracks_per_mix", data.get("mix_tracks_per_mix")),
            100,
            1,
            300,
        ),
        update_cadence=str(data.get("update_cadence") or data.get("mix_update_cadence") or "daily"),
        region_threshold=_bounded_float(data.get("region_threshold", data.get("mix_region_threshold")), 0.82, 0.0, 1.0),
        max_per_artist=_bounded_int(data.get("max_per_artist", data.get("mix_max_per_artist")), 4, 1, 50),
        max_per_release=_bounded_int(data.get("max_per_release", data.get("mix_max_per_release")), 2, 1, 50),
        candidate_pool=_bounded_int(data.get("candidate_pool", data.get("mix_candidate_pool")), 1200, 10, 5000),
        discovery_ratio=_bounded_float(data.get("discovery_ratio", data.get("mix_discovery_ratio")), 0.75, 0.0, 1.0),
        novelty_weight=_bounded_float(data.get("novelty_weight", data.get("mix_novelty_weight")), 0.6, 0.0, 1.0),
        duplicate_strictness=str(data.get("duplicate_strictness") or data.get("mix_duplicate_strictness") or "strict"),
        seed_source=_normalized_seed_source(data.get("seed_source") or data.get("mix_seed_source")),
    )


def build_taste_regions(store: Store, settings: MixSettings) -> tuple[list[TasteRegion], dict[str, object]]:
    seeds, seed_debug = _load_taste_seeds(store, settings)
    if not seeds:
        return [], {**seed_debug, "region_count": 0}

    ordered = sorted(seeds, key=lambda seed: (-seed.signal_score, seed.track.id))
    clusters: list[list[TasteSeed]] = []
    centroids: list[np.ndarray] = []
    for seed in ordered:
        best_index = None
        best_similarity = -1.0
        for index, centroid in enumerate(centroids):
            similarity = _cosine(seed.vector, centroid)
            if similarity > best_similarity:
                best_similarity = similarity
                best_index = index
        if best_index is not None and best_similarity >= settings.region_threshold:
            clusters[best_index].append(seed)
            centroids[best_index] = _normalized_mean([item.vector for item in clusters[best_index]])
        else:
            clusters.append([seed])
            centroids.append(seed.vector)

    regions: list[TasteRegion] = []
    for index, cluster in enumerate(clusters):
        centroid = _normalized_mean([seed.vector for seed in cluster])
        representative = max(cluster, key=lambda seed: (_cosine(seed.vector, centroid), seed.signal_score, -seed.track.id))
        region_id = _stable_region_id([seed.track.id for seed in cluster], settings.model)
        regions.append(
            TasteRegion(
                id=region_id,
                centroid=centroid,
                seeds=cluster,
                representative=representative,
                top_artists=_top_values(seed.track.artist for seed in cluster),
                top_releases=_top_values(seed.track.album for seed in cluster),
                diagnostics={
                    "index": index,
                    "seed_count": len(cluster),
                    "signal_strength": sum(seed.signal_score for seed in cluster),
                    "representative_track_id": representative.track.id,
                    "representative_title": representative.track.title,
                    "label_artists": _top_values(seed.track.artist for seed in cluster),
                    "seed_examples": _seed_examples(cluster, centroid),
                },
            )
        )
    regions.sort(key=lambda region: (-sum(seed.signal_score for seed in region.seeds), -len(region.seeds), region.id))
    return regions, {**seed_debug, "region_count": len(regions)}


def generate_mixes(
    store: Store,
    app_settings: Settings,
    request_settings: dict[str, object] | None = None,
    *,
    count: int | None = None,
    tracks_per_mix: int | None = None,
    force: bool = False,
) -> GeneratedMixResult:
    mix_settings = resolve_mix_settings(request_settings, count=count, tracks_per_mix=tracks_per_mix)
    regions, region_debug = build_taste_regions(store, mix_settings)
    if not regions:
        return GeneratedMixResult([], [], {"settings": mix_settings.__dict__, **region_debug})
    if force:
        store.mark_generated_mixes_stale(mix_type="taste_region")

    anchors = _select_anchor_regions(regions, mix_settings.count)
    ids, vectors = store.load_embeddings(mix_settings.model)
    candidate_source = _prepare_candidate_source(store, app_settings, mix_settings, ids, vectors)
    tracks_by_id = store.get_tracks([int(track_id) for track_id in ids])
    vector_by_id = {int(track_id): _normalized(vector) for track_id, vector in zip(ids, vectors, strict=False)}
    preference_by_id = _track_preference_rows(store)
    used_track_ids: set[int] = set()
    generation_id = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    expires_at = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    saved: list[GeneratedMix] = []
    generator_debug: dict[str, object] = {
        "settings": mix_settings.__dict__,
        "region_count": len(regions),
        "anchor_count": len(anchors),
        "index_dir": str(app_settings.index_dir),
        "candidate_source": candidate_source.diagnostics,
    }

    for index, region in enumerate(anchors):
        candidate_ids = _hnsw_candidate_track_ids(candidate_source, region, mix_settings)
        selected, summary = _generate_region_items(
            store,
            region,
            mix_settings,
            candidate_ids,
            vector_by_id,
            tracks_by_id,
            preference_by_id,
            used_track_ids,
        )
        if mix_settings.duplicate_strictness == "strict":
            used_track_ids.update(int(item["track_id"]) for item in selected)
        if not selected:
            continue
        title = _mix_title(region, index)
        mix_id = f"mix-{generation_id}-{index + 1}-{region.id[:8]}"
        mix = store.save_generated_mix(
            mix_id=mix_id,
            title=title,
            mix_type="taste_region",
            status="active",
            anchor=_region_anchor(region),
            settings=mix_settings.__dict__,
            score_summary=summary,
            items=selected,
            expires_at=expires_at,
        )
        cover_path = generate_mix_cover(
            store,
            app_settings,
            mix_id,
            [int(item["track_id"]) for item in selected[:4]],
        )
        if cover_path is not None:
            mix = store.set_generated_mix_cover_path(mix_id, str(cover_path)) or mix
        saved.append(mix)
    generator_debug["generated_count"] = len(saved)
    generator_debug["used_track_count"] = len(used_track_ids)
    return GeneratedMixResult(saved, regions, generator_debug)


def ensure_dashboard_mixes(
    store: Store,
    app_settings: Settings,
    request_settings: dict[str, object] | None = None,
    *,
    force: bool = False,
) -> DashboardMixEnsureResult:
    plan = dashboard_mix_generation_plan(store, request_settings, force=force)
    mix_settings = plan.mix_settings
    diagnostics = dict(plan.diagnostics)
    if not plan.should_generate:
        return DashboardMixEnsureResult([], diagnostics)
    if plan.generation_count <= 0:
        diagnostics["reason"] = plan.reason
        return DashboardMixEnsureResult([], diagnostics)

    result = generate_mixes(
        store,
        app_settings,
        request_settings,
        count=plan.generation_count,
        tracks_per_mix=mix_settings.tracks_per_mix,
        force=force or plan.reason in {"expired", "preference_changed"},
    )
    diagnostics.update(result.diagnostics)
    diagnostics.update(
        {
            "existing_visible_count": len(plan.existing),
            "active_count": len(plan.active),
            "saved_count": plan.saved_count,
            "expired_active_count": len(plan.expired),
            "preference_state": plan.diagnostics["preference_state"],
            "preference_refresh_due": plan.diagnostics["preference_refresh_due"],
            "generated_count": len(result.mixes),
            "reason": plan.reason if result.mixes else "not_enough_data",
        }
    )
    return DashboardMixEnsureResult(result.mixes, diagnostics)


def dashboard_mix_generation_plan(
    store: Store,
    request_settings: dict[str, object] | None = None,
    *,
    force: bool = False,
) -> DashboardMixGenerationPlan:
    mix_settings = resolve_mix_settings(request_settings)
    existing = store.list_generated_mixes(statuses=["active", "saved"], limit=mix_settings.count, offset=0)
    active = [mix for mix in existing if mix.status == "active"]
    saved_count = sum(1 for mix in existing if mix.status == "saved")
    expired = [mix for mix in active if _mix_expired(mix.expires_at)]
    preference_state = _preference_state(store)
    changed_due = _preference_refresh_due(active, preference_state, mix_settings)
    visible_count = len(existing)
    should_generate = force or bool(expired) or changed_due or visible_count < mix_settings.count
    newest_active = _newest_mix_updated(active)
    partial_backfill_cooling_down = (
        not force
        and not expired
        and not changed_due
        and 0 < visible_count < mix_settings.count
        and newest_active is not None
        and datetime.now(UTC) - newest_active < timedelta(days=1)
    )
    if partial_backfill_cooling_down:
        should_generate = False
        reason = "fresh_partial"
    elif force:
        reason = "force"
    elif expired:
        reason = "expired"
    elif changed_due:
        reason = "preference_changed"
    elif visible_count < mix_settings.count:
        reason = "missing"
    else:
        reason = "fresh"
    generation_count = (
        mix_settings.count
        if reason in {"force", "expired", "preference_changed"}
        else max(0, mix_settings.count - saved_count - len(active))
    )
    diagnostics: dict[str, object] = {
        "settings": mix_settings.__dict__,
        "existing_visible_count": visible_count,
        "active_count": len(active),
        "saved_count": saved_count,
        "expired_active_count": len(expired),
        "preference_state": preference_state,
        "preference_refresh_due": changed_due,
        "partial_backfill_cooling_down": partial_backfill_cooling_down,
        "should_generate": should_generate,
        "generation_count": generation_count,
        "generated_count": 0,
        "reason": reason,
        "newest_active_at": newest_active.isoformat() if newest_active else None,
    }
    return DashboardMixGenerationPlan(
        mix_settings=mix_settings,
        existing=existing,
        active=active,
        saved_count=saved_count,
        expired=expired,
        diagnostics=diagnostics,
        should_generate=should_generate,
        generation_count=generation_count,
        reason=reason,
    )


def _mix_expired(expires_at: str | None) -> bool:
    expires = _parse_datetime(expires_at)
    if expires is None:
        return False
    return expires <= datetime.now(UTC)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _preference_state(store: Store) -> dict[str, object]:
    with store.connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count, MAX(updated_at) AS max_updated_at
            FROM user_track_preferences
            WHERE user_id = discocs_user_id() AND disliked = 0
              AND (
                    liked = 1 OR completion_count > 0 OR replay_count > 0 OR score > 0
                    OR play_count > 0 OR last_played_at IS NOT NULL
                  )
            """
        ).fetchone()
    return {
        "positive_count": int(row["count"] or 0),
        "max_updated_at": row["max_updated_at"],
    }


def _preference_refresh_due(active: list[GeneratedMix], state: dict[str, object], settings: MixSettings) -> bool:
    if not active:
        return False
    max_pref = _parse_datetime(str(state.get("max_updated_at") or ""))
    if max_pref is None:
        return False
    newest_mix = _newest_mix_updated(active)
    if newest_mix is None or max_pref <= newest_mix:
        return False
    cadence = settings.update_cadence.lower()
    if cadence == "manual":
        return False
    threshold = timedelta(days=1 if cadence == "daily" else 7)
    return datetime.now(UTC) - newest_mix >= threshold


def _newest_mix_updated(active: list[GeneratedMix]) -> datetime | None:
    parsed = [_parse_datetime(mix.updated_at) for mix in active]
    parsed = [value for value in parsed if value is not None]
    return max(parsed, default=None)


def _prepare_candidate_source(
    _store: Store,
    app_settings: Settings,
    settings: MixSettings,
    ids: np.ndarray,
    vectors: np.ndarray,
) -> CandidateSource:
    diagnostics: dict[str, object] = {
        "type": "none",
        "model": settings.model,
        "embedding_count": int(len(ids)),
        "uses_hnsw": False,
    }
    if len(ids) == 0 or vectors.ndim != 2:
        return CandidateSource(None, ids, vectors, diagnostics)
    index_path = app_settings.index_path(settings.model)
    index: HnswIndex | None = None
    if index_path.exists():
        try:
            loaded = HnswIndex.load(index_path, dim=int(vectors.shape[1]), ef=max(50, settings.candidate_pool))
            if loaded.count() == len(ids):
                index = loaded
                diagnostics.update({"type": "persisted", "path": str(index_path), "uses_hnsw": True})
            else:
                diagnostics.update(
                    {
                        "type": "persisted_stale",
                        "path": str(index_path),
                        "index_count": loaded.count(),
                    }
                )
        except Exception as exc:
            diagnostics.update({"type": "persisted_error", "path": str(index_path), "error": str(exc)})
    if index is None:
        index = HnswIndex.build(ids.astype(np.int64), vectors.astype(np.float32))
        diagnostics.update({"type": "transient", "uses_hnsw": True})
    return CandidateSource(index, ids, vectors, diagnostics)


def _hnsw_candidate_track_ids(source: CandidateSource, region: TasteRegion, settings: MixSettings) -> list[int]:
    if source.index is None:
        return [int(track_id) for track_id in source.ids[: settings.candidate_pool]]
    query_vectors = [region.centroid, region.representative.vector]
    query_vectors.extend(seed.vector for seed in region.seeds[:3])
    per_query = max(settings.tracks_per_mix * 4, settings.candidate_pool // max(1, len(query_vectors)))
    per_query = max(per_query, min(settings.candidate_pool, 50))
    candidates: dict[int, float] = {}
    for query_vector in query_vectors:
        labels, distances = source.index.query(query_vector, min(settings.candidate_pool, per_query))
        for label, distance in zip(labels, distances, strict=False):
            track_id = int(label)
            similarity = 1.0 - float(distance)
            candidates[track_id] = max(candidates.get(track_id, -1.0), similarity)
    for seed in region.seeds:
        candidates[seed.track.id] = max(candidates.get(seed.track.id, -1.0), 1.0)
    return [
        track_id
        for track_id, _similarity in sorted(candidates.items(), key=lambda item: (-item[1], item[0]))[
            : settings.candidate_pool
        ]
    ]


def _generate_region_items(
    store: Store,
    region: TasteRegion,
    settings: MixSettings,
    candidate_ids: list[int],
    vector_by_id: dict[int, np.ndarray],
    tracks_by_id: dict[int, Track],
    preference_by_id: dict[int, dict[str, object]],
    used_track_ids: set[int],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    seed_ids = {seed.track.id for seed in region.seeds}
    candidates: list[tuple[float, Track, dict[str, float], dict[str, object]]] = []
    for track_id in candidate_ids:
        vector = vector_by_id.get(track_id)
        if vector is None:
            continue
        track = tracks_by_id.get(track_id)
        if track is None or track.missing_at is not None or track.duration is not None and track.duration < 60.0:
            continue
        region_similarity = _cosine(vector, region.centroid)
        pref = preference_by_id.get(track_id, {})
        preference_score = float(pref.get("score") or 0.0)
        liked = bool(pref.get("liked"))
        completion_count = int(pref.get("completion_count") or 0)
        replay_count = int(pref.get("replay_count") or 0)
        play_count = int(pref.get("play_count") or 0)
        known = track_id in seed_ids or liked or completion_count > 0 or replay_count > 0 or play_count > 0 or bool(pref.get("last_played_at"))
        novelty_score = _novelty_score(pref)
        novelty_weight = settings.novelty_weight
        discovery_bonus = 0.2 * novelty_weight * novelty_score
        familiarity_penalty = 0.08 * novelty_weight * (1.0 - novelty_score)
        freshness_bonus = (0.06 * novelty_weight) if novelty_score >= 0.95 else 0.0
        recent_skip_penalty = 0.35 if pref.get("last_skipped_at") and not liked else 0.0
        cross_mix_duplicate_penalty = 1.0 if track_id in used_track_ids else 0.0
        score = (
            region_similarity
            + min(preference_score, 5.0) * 0.04
            + discovery_bonus
            + freshness_bonus
            - familiarity_penalty
            - recent_skip_penalty
            - cross_mix_duplicate_penalty
        )
        breakdown = {
            "region_similarity": region_similarity,
            "user_preference_score": preference_score,
            "novelty_score": novelty_score,
            "novelty_weight": novelty_weight,
            "discovery_bonus": discovery_bonus,
            "familiarity_penalty": familiarity_penalty,
            "freshness_bonus": freshness_bonus,
            "recent_skip_penalty": recent_skip_penalty,
            "cross_mix_duplicate_penalty": cross_mix_duplicate_penalty,
        }
        reason = {
            "anchor_region_id": region.id,
            "anchor_track_id": region.representative.track.id,
            "representative_title": region.representative.track.title,
            "known": known,
            "novelty_score": novelty_score,
            "novelty_bucket": _novelty_bucket(pref, track_id in seed_ids),
            "seed_track": track_id in seed_ids,
        }
        candidates.append((score, track, breakdown, reason))

    candidates.sort(key=lambda item: (-item[0], item[1].id))
    candidates = candidates[: settings.candidate_pool]
    selected: list[dict[str, object]] = []
    artist_counts: dict[int, int] = {}
    release_counts: dict[int, int] = {}
    skipped_artist_cap = 0
    skipped_release_cap = 0
    skipped_duplicate = 0
    skipped_known_quota = 0
    discovery_target = int(round(settings.tracks_per_mix * settings.discovery_ratio))
    known_quota = max(0, settings.tracks_per_mix - discovery_target)
    known_selected = 0
    new_selected = 0
    novelty_total = 0.0
    novelty_distribution = {
        "unheard": 0,
        "old_heard": 0,
        "recent_heard": 0,
        "liked_or_seed": 0,
    }
    deferred_known: list[tuple[float, Track, dict[str, float], dict[str, object]]] = []
    for score, track, breakdown, reason in candidates:
        if settings.duplicate_strictness == "strict" and track.id in used_track_ids:
            skipped_duplicate += 1
            continue
        known = bool(reason.get("known"))
        if known and known_selected >= known_quota:
            skipped_known_quota += 1
            deferred_known.append((score, track, breakdown, reason))
            continue
        artist_ids = store.artist_ids_for_track(track.id)
        release_id = store.release_id_for_track(track.id)
        if artist_ids and any(artist_counts.get(artist_id, 0) >= settings.max_per_artist for artist_id in artist_ids):
            skipped_artist_cap += 1
            continue
        if release_id is not None and release_counts.get(release_id, 0) >= settings.max_per_release:
            skipped_release_cap += 1
            continue
        position = len(selected)
        selected.append(
            {
                "position": position,
                "track_id": track.id,
                "score": score,
                "score_breakdown": breakdown,
                "reason": reason,
            }
        )
        for artist_id in artist_ids:
            artist_counts[artist_id] = artist_counts.get(artist_id, 0) + 1
        if release_id is not None:
            release_counts[release_id] = release_counts.get(release_id, 0) + 1
        novelty_total += float(reason.get("novelty_score") or 0.0)
        bucket = str(reason.get("novelty_bucket") or "unheard")
        novelty_distribution[bucket] = novelty_distribution.get(bucket, 0) + 1
        if known:
            known_selected += 1
        else:
            new_selected += 1
        if len(selected) >= settings.tracks_per_mix:
            break

    for score, track, breakdown, reason in deferred_known:
        if len(selected) >= settings.tracks_per_mix:
            break
        if settings.duplicate_strictness == "strict" and track.id in used_track_ids:
            continue
        if any(int(item["track_id"]) == track.id for item in selected):
            continue
        artist_ids = store.artist_ids_for_track(track.id)
        release_id = store.release_id_for_track(track.id)
        if artist_ids and any(artist_counts.get(artist_id, 0) >= settings.max_per_artist for artist_id in artist_ids):
            continue
        if release_id is not None and release_counts.get(release_id, 0) >= settings.max_per_release:
            continue
        position = len(selected)
        selected.append(
            {
                "position": position,
                "track_id": track.id,
                "score": score,
                "score_breakdown": breakdown,
                "reason": reason,
            }
        )
        for artist_id in artist_ids:
            artist_counts[artist_id] = artist_counts.get(artist_id, 0) + 1
        if release_id is not None:
            release_counts[release_id] = release_counts.get(release_id, 0) + 1
        novelty_total += float(reason.get("novelty_score") or 0.0)
        bucket = str(reason.get("novelty_bucket") or "unheard")
        novelty_distribution[bucket] = novelty_distribution.get(bucket, 0) + 1
        known_selected += 1

    selected = _sequence_items(store, selected)
    summary = {
        **region.diagnostics,
        "region_id": region.id,
        "candidate_count": len(candidates),
        "candidate_id_count": len(candidate_ids),
        "selected_count": len(selected),
        "skipped_artist_cap": skipped_artist_cap,
        "skipped_release_cap": skipped_release_cap,
        "skipped_cross_mix_duplicate": skipped_duplicate,
        "skipped_known_quota": skipped_known_quota,
        "known_selected": known_selected,
        "new_selected": new_selected,
        "discovery_target": discovery_target,
        "novelty_weight": settings.novelty_weight,
        "average_novelty": novelty_total / len(selected) if selected else 0.0,
        "novelty_distribution": novelty_distribution,
        "representative_track": {
            "id": region.representative.track.id,
            "title": region.representative.track.title,
            "artist": region.representative.track.artist,
            "album": region.representative.track.album,
        },
        "seed_examples": region.diagnostics.get("seed_examples", []),
    }
    return selected, summary


def _sequence_items(store: Store, items: list[dict[str, object]]) -> list[dict[str, object]]:
    remaining = list(items)
    sequenced: list[dict[str, object]] = []
    last_artist_ids: set[int] = set()
    last_release_id: int | None = None
    while remaining:
        chosen_index = 0
        for index, item in enumerate(remaining):
            track_id = int(item["track_id"])
            artist_ids = set(store.artist_ids_for_track(track_id))
            release_id = store.release_id_for_track(track_id)
            if artist_ids.isdisjoint(last_artist_ids) and release_id != last_release_id:
                chosen_index = index
                break
        chosen = remaining.pop(chosen_index)
        chosen["position"] = len(sequenced)
        sequenced.append(chosen)
        chosen_track_id = int(chosen["track_id"])
        last_artist_ids = set(store.artist_ids_for_track(chosen_track_id))
        last_release_id = store.release_id_for_track(chosen_track_id)
    return sequenced


def _load_taste_seeds(store: Store, settings: MixSettings) -> tuple[list[TasteSeed], dict[str, object]]:
    seed_where = _seed_source_where(settings.seed_source)
    with store.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT t.*, p.liked, p.disliked, p.play_count, p.completion_count,
                   p.skip_count, p.early_skip_count, p.replay_count, p.score,
                   p.last_played_at, p.last_completed_at, p.last_skipped_at,
                   e.dim, e.vector
            FROM user_track_preferences p
            JOIN tracks t ON t.id = p.track_id
            JOIN embeddings e ON e.track_id = t.id AND e.model_name = ?
            WHERE p.user_id = discocs_user_id() AND t.missing_at IS NULL
              AND p.disliked = 0
              AND ({seed_where})
            ORDER BY p.liked DESC, p.score DESC, p.play_count DESC, p.completion_count DESC, t.id
            """,
            (settings.model,),
        ).fetchall()
        fallback = False
        if not rows:
            fallback = True
            rows = conn.execute(
                """
                SELECT t.*, 0 AS liked, 0 AS disliked, 0 AS play_count,
                       0 AS completion_count, 0 AS skip_count, 0 AS early_skip_count,
                       0 AS replay_count, 0.0 AS score, NULL AS last_played_at,
                       NULL AS last_completed_at, NULL AS last_skipped_at,
                       e.dim, e.vector
                FROM embeddings e
                JOIN tracks t ON t.id = e.track_id
                WHERE e.model_name = ? AND t.missing_at IS NULL
                ORDER BY t.id
                LIMIT 200
                """,
                (settings.model,),
            ).fetchall()
    seeds: list[TasteSeed] = []
    for row in rows:
        vector = np.frombuffer(row["vector"], dtype=np.float32, count=int(row["dim"])).copy()
        signal = _seed_signal(row)
        signal_score = _seed_signal_score(signal)
        if fallback:
            signal_score = 0.1
        seeds.append(
            TasteSeed(
                track=Track(
                    id=int(row["id"]),
                    path=str(row["path"]),
                    artist=row["artist"],
                    title=row["title"],
                    album=row["album"],
                    genre=row["genre"],
                    year=int(row["year"]) if row["year"] is not None else None,
                    duration=float(row["duration"]) if row["duration"] is not None else None,
                    file_size=int(row["file_size"]),
                    mtime=int(row["mtime"]),
                    missing_at=row["missing_at"],
                    added_at=row["added_at"] if "added_at" in row.keys() else None,
                ),
                vector=_normalized(vector),
                signal_score=signal_score,
                signal=signal,
            )
        )
    return seeds, {"seed_count": len(seeds), "seed_source": settings.seed_source, "fallback_to_embedded_tracks": fallback}


def _normalized_seed_source(value: object) -> str:
    source = str(value or "listening_history").strip().lower()
    if source in {"listening_history", "track_likes_only", "positive_history"}:
        return source
    return "listening_history"


def _seed_source_where(seed_source: str) -> str:
    if seed_source == "track_likes_only":
        return "p.liked = 1"
    if seed_source == "positive_history":
        return "p.liked = 1 OR p.completion_count > 0 OR p.replay_count > 0 OR p.score > 0"
    return (
        "p.liked = 1 OR p.completion_count > 0 OR p.replay_count > 0 OR p.score > 0 "
        "OR p.play_count > 0 OR p.last_played_at IS NOT NULL"
    )


def _seed_signal(row) -> dict[str, object]:
    return {
        "liked": bool(row["liked"]),
        "play_count": int(row["play_count"] or 0),
        "completion_count": int(row["completion_count"] or 0),
        "replay_count": int(row["replay_count"] or 0),
        "skip_count": int(row["skip_count"] or 0),
        "early_skip_count": int(row["early_skip_count"] or 0),
        "score": float(row["score"] or 0.0),
        "last_played_at": row["last_played_at"],
        "last_completed_at": row["last_completed_at"],
        "last_skipped_at": row["last_skipped_at"],
    }


def _seed_signal_score(signal: dict[str, object]) -> float:
    play_count = int(signal.get("play_count") or 0)
    score = (
        (3.0 if bool(signal.get("liked")) else 0.0)
        + min(float(signal.get("score") or 0.0), 8.0)
        + int(signal.get("completion_count") or 0) * 0.6
        + int(signal.get("replay_count") or 0) * 0.9
        + float(np.log1p(play_count)) * 0.75
    )
    if play_count == 0 and signal.get("last_played_at"):
        score += 0.35
    score -= int(signal.get("skip_count") or 0) * 0.2
    score -= int(signal.get("early_skip_count") or 0) * 0.6
    return max(0.05, score)


def _novelty_score(pref: dict[str, object]) -> float:
    if not pref:
        return 1.0
    play_count = int(pref.get("play_count") or 0)
    liked = bool(pref.get("liked"))
    completion_count = int(pref.get("completion_count") or 0)
    replay_count = int(pref.get("replay_count") or 0)
    last_played = _parse_datetime(str(pref.get("last_played_at") or ""))
    if play_count <= 0 and not last_played and not liked and completion_count <= 0 and replay_count <= 0:
        return 1.0
    if last_played is None:
        novelty = 0.55
    else:
        days = max(0.0, (datetime.now(UTC) - last_played).total_seconds() / 86400.0)
        if days >= 365:
            novelty = 0.75
        elif days >= 120:
            novelty = 0.6
        elif days >= 30:
            novelty = 0.4
        else:
            novelty = 0.2
    novelty -= min(float(np.log1p(play_count)) * 0.08, 0.35)
    if liked or completion_count > 0 or replay_count > 0:
        novelty -= 0.08
    return max(0.0, min(1.0, novelty))


def _novelty_bucket(pref: dict[str, object], seed_track: bool) -> str:
    if seed_track or bool(pref.get("liked")):
        return "liked_or_seed"
    play_count = int(pref.get("play_count") or 0)
    completion_count = int(pref.get("completion_count") or 0)
    replay_count = int(pref.get("replay_count") or 0)
    last_played = _parse_datetime(str(pref.get("last_played_at") or ""))
    if play_count <= 0 and not last_played and completion_count <= 0 and replay_count <= 0:
        return "unheard"
    if last_played is None:
        return "old_heard"
    days = max(0.0, (datetime.now(UTC) - last_played).total_seconds() / 86400.0)
    return "old_heard" if days >= 120 else "recent_heard"


def _seed_examples(seeds: list[TasteSeed], centroid: np.ndarray, limit: int = 5) -> list[dict[str, object]]:
    ordered = sorted(
        seeds,
        key=lambda seed: (-_cosine(seed.vector, centroid), -seed.signal_score, seed.track.id),
    )
    return [
        {
            "track_id": seed.track.id,
            "title": seed.track.title,
            "artist": seed.track.artist,
            "album": seed.track.album,
            "signal_score": seed.signal_score,
        }
        for seed in ordered[:limit]
    ]


def _track_preference_rows(store: Store) -> dict[int, dict[str, object]]:
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM user_track_preferences WHERE user_id = discocs_user_id()"
        ).fetchall()
    return {int(row["track_id"]): dict(row) for row in rows}


def _select_anchor_regions(regions: list[TasteRegion], count: int) -> list[TasteRegion]:
    if count <= 0:
        return []
    if not regions:
        return []
    max_signal = max(sum(seed.signal_score for seed in region.seeds) for region in regions) or 1.0
    min_anchor_signal = max(2.0, max_signal * 0.025)
    min_anchor_seeds = 3 if len(regions) >= count * 3 else 1
    selected: list[TasteRegion] = []
    remaining = list(regions)
    selected.append(remaining.pop(0))
    while remaining and len(selected) < count:
        best_index = 0
        best_score = -1.0
        for index, region in enumerate(remaining):
            min_distance = min(1.0 - _cosine(region.centroid, existing.centroid) for existing in selected)
            raw_signal = sum(seed.signal_score for seed in region.seeds)
            signal = raw_signal / max_signal
            support = min(1.0, len(region.seeds) / 12.0)
            coverage_bonus = 0.14 if min_distance >= 0.18 and raw_signal >= min_anchor_signal else 0.0
            score = (
                min_distance * 0.48
                + signal * 0.32
                + support * 0.14
                + coverage_bonus
            )
            if len(selected) < min(count, 3):
                score += min_distance * 0.16
            if raw_signal < min_anchor_signal:
                score -= 0.28
            if len(region.seeds) < min_anchor_seeds:
                score -= 0.22
            if score > best_score:
                best_score = score
                best_index = index
        selected.append(remaining.pop(best_index))
    if len(selected) < count:
        selected_ids = {region.id for region in selected}
        for region in regions:
            if len(selected) >= count:
                break
            if region.id not in selected_ids:
                selected.append(region)
                selected_ids.add(region.id)
    if len(selected) < count:
        selected_seed_ids = {region.representative.track.id for region in selected}
        subregion_index = 0
        candidate_seeds = sorted(
            (seed for region in regions for seed in region.seeds if seed.track.id not in selected_seed_ids),
            key=lambda seed: (-seed.signal_score, seed.track.id),
        )
        for seed in candidate_seeds:
            if len(selected) >= count:
                break
            min_distance = min(1.0 - _cosine(seed.vector, region.centroid) for region in selected)
            if min_distance < 0.02 and len(selected) >= min(count, 3):
                continue
            subregion_index += 1
            selected.append(_seed_subregion(seed, subregion_index))
    return selected


def _region_overlap(values: list[str], selected_values: list[str]) -> float:
    own = {_normalized_label(value) for value in values if _normalized_label(value)}
    selected = {_normalized_label(value) for value in selected_values if _normalized_label(value)}
    if not own or not selected:
        return 0.0
    return len(own & selected) / max(1, min(len(own), len(selected)))


def _normalized_label(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _seed_subregion(seed: TasteSeed, index: int) -> TasteRegion:
    return TasteRegion(
        id=f"subregion-{seed.track.id}-{index}",
        centroid=seed.vector,
        seeds=[seed],
        representative=seed,
        top_artists=_top_values([seed.track.artist]),
        top_releases=_top_values([seed.track.album]),
        diagnostics={
            "index": index,
            "seed_count": 1,
            "signal_strength": seed.signal_score,
            "representative_track_id": seed.track.id,
            "representative_title": seed.track.title,
            "label_artists": _top_values([seed.track.artist]),
            "seed_examples": _seed_examples([seed], seed.vector),
            "subregion": True,
        },
    )


def _region_anchor(region: TasteRegion) -> dict[str, object]:
    return {
        "region_id": region.id,
        "seed_track_ids": [seed.track.id for seed in region.seeds],
        "representative_track_id": region.representative.track.id,
        "representative_title": region.representative.track.title,
        "representative_artist": region.representative.track.artist,
        "representative_album": region.representative.track.album,
        "label_artists": region.diagnostics.get("label_artists", region.top_artists),
        "seed_examples": region.diagnostics.get("seed_examples", []),
        "diagnostics": region.diagnostics,
    }


def _mix_title(region: TasteRegion, index: int) -> str:
    label = ", ".join(region.top_artists[:2])
    if not label:
        label = region.representative.track.title or f"Region {index + 1}"
    return f"Mix {index + 1}: {label}"


def _stable_region_id(track_ids: list[int], model: str) -> str:
    digest = hashlib.sha1(f"{model}:{','.join(str(track_id) for track_id in sorted(track_ids))}".encode("utf-8")).hexdigest()
    return f"region-{digest[:16]}"


def _top_values(values) -> list[str]:
    counts: dict[str, int] = {}
    for value in values:
        if not value:
            continue
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return [value for value, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:5]]


def _normalized(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    if norm == 0.0:
        return vector
    return (vector / norm).astype(np.float32)


def _normalized_mean(vectors: list[np.ndarray]) -> np.ndarray:
    if not vectors:
        raise ValueError("No vectors")
    return _normalized(np.mean(np.vstack(vectors), axis=0))


def _cosine(first: np.ndarray, second: np.ndarray) -> float:
    return float(np.dot(_normalized(first), _normalized(second)))


def _bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _bounded_float(value: object, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))
