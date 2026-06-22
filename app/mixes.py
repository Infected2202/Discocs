from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np

from app.config import Settings
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
    region_threshold: float = DEFAULT_REGION_THRESHOLD
    max_per_artist: int = DEFAULT_MAX_PER_ARTIST
    max_per_release: int = DEFAULT_MAX_PER_RELEASE
    candidate_pool: int = DEFAULT_CANDIDATE_POOL
    discovery_ratio: float = 0.5
    duplicate_strictness: str = "strict"


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


def generated_mix_default_settings() -> dict[str, object]:
    return {
        "mix_dashboard_count": DEFAULT_DASHBOARD_MIXES,
        "mix_tracks_per_mix": DEFAULT_TRACKS_PER_MIX,
        "mix_update_cadence": "weekly",
        "mix_region_threshold": DEFAULT_REGION_THRESHOLD,
        "mix_discovery_ratio": 0.5,
        "mix_duplicate_strictness": "strict",
        "mix_max_per_artist": DEFAULT_MAX_PER_ARTIST,
        "mix_max_per_release": DEFAULT_MAX_PER_RELEASE,
        "mix_include_small_regions": True,
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
        region_threshold=_bounded_float(data.get("region_threshold", data.get("mix_region_threshold")), 0.82, 0.0, 1.0),
        max_per_artist=_bounded_int(data.get("max_per_artist", data.get("mix_max_per_artist")), 4, 1, 50),
        max_per_release=_bounded_int(data.get("max_per_release", data.get("mix_max_per_release")), 2, 1, 50),
        candidate_pool=_bounded_int(data.get("candidate_pool", data.get("mix_candidate_pool")), 1200, 10, 5000),
        discovery_ratio=_bounded_float(data.get("discovery_ratio", data.get("mix_discovery_ratio")), 0.5, 0.0, 1.0),
        duplicate_strictness=str(data.get("duplicate_strictness") or data.get("mix_duplicate_strictness") or "strict"),
    )


def build_taste_regions(store: Store, settings: MixSettings) -> tuple[list[TasteRegion], dict[str, object]]:
    seeds, seed_debug = _load_taste_seeds(store, settings.model)
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
    tracks_by_id = store.get_tracks([int(track_id) for track_id in ids])
    vector_by_id = {int(track_id): _normalized(vector) for track_id, vector in zip(ids, vectors, strict=False)}
    preference_by_id = _track_preference_rows(store)
    used_track_ids: set[int] = set()
    generation_id = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    expires_at = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    saved: list[GeneratedMix] = []
    generator_debug: dict[str, object] = {
        "settings": mix_settings.__dict__,
        "region_count": len(regions),
        "anchor_count": len(anchors),
        "index_dir": str(app_settings.index_dir),
        "fallback_exact_vector_scan": True,
    }

    for index, region in enumerate(anchors):
        selected, summary = _generate_region_items(
            store,
            region,
            mix_settings,
            vector_by_id,
            tracks_by_id,
            preference_by_id,
            used_track_ids,
        )
        if mix_settings.duplicate_strictness == "strict":
            used_track_ids.update(int(item["track_id"]) for item in selected)
        title = _mix_title(region, index)
        mix_id = f"mix-{generation_id}-{index + 1}-{region.id[:8]}"
        saved.append(
            store.save_generated_mix(
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
        )
    generator_debug["generated_count"] = len(saved)
    generator_debug["used_track_count"] = len(used_track_ids)
    return GeneratedMixResult(saved, regions, generator_debug)


def _generate_region_items(
    store: Store,
    region: TasteRegion,
    settings: MixSettings,
    vector_by_id: dict[int, np.ndarray],
    tracks_by_id: dict[int, Track],
    preference_by_id: dict[int, dict[str, object]],
    used_track_ids: set[int],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    seed_ids = {seed.track.id for seed in region.seeds}
    candidates: list[tuple[float, Track, dict[str, float], dict[str, object]]] = []
    for track_id, vector in vector_by_id.items():
        track = tracks_by_id.get(track_id)
        if track is None or track.missing_at is not None or track.duration is not None and track.duration < 60.0:
            continue
        region_similarity = _cosine(vector, region.centroid)
        pref = preference_by_id.get(track_id, {})
        preference_score = float(pref.get("score") or 0.0)
        liked = bool(pref.get("liked"))
        completion_count = int(pref.get("completion_count") or 0)
        replay_count = int(pref.get("replay_count") or 0)
        familiar = track_id in seed_ids or liked or completion_count > 0 or replay_count > 0
        discovery_bonus = 0.08 if not familiar else 0.0
        freshness_bonus = 0.03 if not pref.get("last_played_at") else 0.0
        recent_skip_penalty = 0.35 if pref.get("last_skipped_at") and not liked else 0.0
        cross_mix_duplicate_penalty = 1.0 if track_id in used_track_ids else 0.0
        score = (
            region_similarity
            + min(preference_score, 5.0) * 0.04
            + discovery_bonus
            + freshness_bonus
            - recent_skip_penalty
            - cross_mix_duplicate_penalty
        )
        breakdown = {
            "region_similarity": region_similarity,
            "user_preference_score": preference_score,
            "discovery_bonus": discovery_bonus,
            "freshness_bonus": freshness_bonus,
            "recent_skip_penalty": recent_skip_penalty,
            "cross_mix_duplicate_penalty": cross_mix_duplicate_penalty,
        }
        reason = {
            "anchor_region_id": region.id,
            "anchor_track_id": region.representative.track.id,
            "anchor_artists": region.top_artists,
            "familiar": familiar,
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
    for score, track, breakdown, reason in candidates:
        if settings.duplicate_strictness == "strict" and track.id in used_track_ids:
            skipped_duplicate += 1
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
        if len(selected) >= settings.tracks_per_mix:
            break

    selected = _sequence_items(store, selected)
    summary = {
        **region.diagnostics,
        "region_id": region.id,
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "skipped_artist_cap": skipped_artist_cap,
        "skipped_release_cap": skipped_release_cap,
        "skipped_cross_mix_duplicate": skipped_duplicate,
        "top_artists": region.top_artists,
        "top_releases": region.top_releases,
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


def _load_taste_seeds(store: Store, model: str) -> tuple[list[TasteSeed], dict[str, object]]:
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT t.*, p.liked, p.disliked, p.play_count, p.completion_count,
                   p.skip_count, p.early_skip_count, p.replay_count, p.score,
                   p.last_played_at, p.last_completed_at, p.last_skipped_at,
                   e.dim, e.vector
            FROM user_track_preferences p
            JOIN tracks t ON t.id = p.track_id
            JOIN embeddings e ON e.track_id = t.id AND e.model_name = ?
            WHERE t.missing_at IS NULL
              AND p.disliked = 0
              AND (p.liked = 1 OR p.completion_count > 0 OR p.replay_count > 0 OR p.score > 0)
            ORDER BY p.liked DESC, p.score DESC, p.completion_count DESC, t.id
            """,
            (model,),
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
                (model,),
            ).fetchall()
    seeds: list[TasteSeed] = []
    for row in rows:
        vector = np.frombuffer(row["vector"], dtype=np.float32, count=int(row["dim"])).copy()
        signal_score = (
            (3.0 if int(row["liked"] or 0) else 0.0)
            + float(row["score"] or 0.0)
            + int(row["completion_count"] or 0) * 0.5
            + int(row["replay_count"] or 0) * 0.75
            + int(row["play_count"] or 0) * 0.1
        )
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
                signal={
                    "liked": bool(row["liked"]),
                    "play_count": int(row["play_count"] or 0),
                    "completion_count": int(row["completion_count"] or 0),
                    "replay_count": int(row["replay_count"] or 0),
                    "score": float(row["score"] or 0.0),
                },
            )
        )
    return seeds, {"seed_count": len(seeds), "fallback_to_embedded_tracks": fallback}


def _track_preference_rows(store: Store) -> dict[int, dict[str, object]]:
    with store.connect() as conn:
        rows = conn.execute("SELECT * FROM user_track_preferences").fetchall()
    return {int(row["track_id"]): dict(row) for row in rows}


def _select_anchor_regions(regions: list[TasteRegion], count: int) -> list[TasteRegion]:
    selected: list[TasteRegion] = []
    for region in regions:
        if len(selected) >= count:
            break
        if not selected:
            selected.append(region)
            continue
        min_distance = min(1.0 - _cosine(region.centroid, existing.centroid) for existing in selected)
        if min_distance >= 0.08 or len(selected) < min(count, 3):
            selected.append(region)
    for region in regions:
        if len(selected) >= count:
            break
        if region not in selected:
            selected.append(region)
    return selected


def _region_anchor(region: TasteRegion) -> dict[str, object]:
    return {
        "region_id": region.id,
        "seed_track_ids": [seed.track.id for seed in region.seeds],
        "representative_track_id": region.representative.track.id,
        "representative_title": region.representative.track.title,
        "top_artists": region.top_artists,
        "top_releases": region.top_releases,
        "diagnostics": region.diagnostics,
    }


def _mix_title(region: TasteRegion, index: int) -> str:
    artists = region.top_artists[:2]
    if artists:
        return f"Mix {index + 1}: {', '.join(artists)}"
    title = region.representative.track.title or f"Region {index + 1}"
    return f"Mix {index + 1}: {title}"


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
