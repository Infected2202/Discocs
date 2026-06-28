from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

import numpy as np

from app.config import Settings
from app.recommender import Recommender
from app.store import PlaybackEvent, PlaybackSession, QueueItem, Store, Track


AUTOPLAY_SOURCE_TYPES = {"track", "release", "artist", "playlist", "search", "manual", "generated_mix", "flow"}
POSITIVE_EVENTS = {"completed", "liked", "play_threshold_reached", "replayed"}
NEGATIVE_EVENTS = {"skipped", "disliked"}
AUTOPLAY_POOL_STATE_KEY = "autoplay_pool"
AUTOPLAY_POOL_LOW_WATERMARK_RATIO = 0.2


@dataclass(frozen=True)
class AutoplaySettings:
    model: str = "discogs_multi"
    visible_buffer: int = 5
    candidate_count: int = 200
    source_weight: float = 0.8
    accepted_weight: float = 0.35
    personal_weight: float = 0.2
    exploration_ratio: float = 0.08
    max_per_artist: int = 2
    max_per_release: int = 1
    recent_skip_penalty: float = 0.45
    preference_chip: str = "All"


@dataclass(frozen=True)
class SourceContext:
    source_type: str
    source_id: int | None
    source_track_ids: list[int]
    seed_track_ids: list[int]
    accepted_track_ids: list[int]
    skipped_track_ids: list[int]
    played_track_ids: list[int]
    existing_queue_track_ids: list[int]
    current_track_ids: list[int]
    exclude_track_ids: set[int]
    source_debug: dict[str, object]


@dataclass(frozen=True)
class AutoplayCandidate:
    track: Track
    source_similarity: float
    accepted_similarity: float
    personal_score: float
    score: float
    reason: str
    debug: dict[str, object]


@dataclass(frozen=True)
class AutoplayRefillResult:
    session_id: str
    added_items: list[QueueItem]
    candidate_count: int
    debug: dict[str, object] | None = None


def autoplay_default_settings() -> dict[str, object]:
    return {
        "autoplay_enabled": True,
        "autoplay_visible_buffer": 5,
        "autoplay_candidate_count": 200,
        "autoplay_source_weight": 0.8,
        "autoplay_accepted_weight": 0.35,
        "autoplay_personal_weight": 0.2,
        "autoplay_exploration_ratio": 0.08,
        "autoplay_max_per_artist": 2,
        "autoplay_max_per_release": 1,
        "autoplay_recent_skip_penalty": 0.45,
        "autoplay_preference_chip": "All",
    }


def resolve_autoplay_settings(
    session: PlaybackSession,
    request_settings: dict[str, object] | None = None,
    *,
    visible_buffer: int | None = None,
    candidate_count: int | None = None,
) -> AutoplaySettings:
    values = _session_settings(session)
    values.update(request_settings or {})
    chip = str(values.get("preference_chip") or values.get("autoplay_preference_chip") or "All")
    source_weight = _bounded_float(
        values.get("source_weight", values.get("autoplay_source_weight")),
        default=0.8,
        minimum=0.0,
        maximum=2.0,
    )
    personal_weight = _bounded_float(
        values.get("personal_weight", values.get("autoplay_personal_weight")),
        default=0.2,
        minimum=0.0,
        maximum=2.0,
    )
    accepted_weight = _bounded_float(
        values.get("accepted_weight", values.get("autoplay_accepted_weight")),
        default=0.35,
        minimum=0.0,
        maximum=2.0,
    )
    exploration = _bounded_float(
        values.get("exploration_ratio", values.get("autoplay_exploration_ratio")),
        default=0.08,
        minimum=0.0,
        maximum=1.0,
    )
    if chip == "Familiar":
        personal_weight *= 1.4
        exploration *= 0.5
    elif chip == "Recommended":
        source_weight *= 1.1
        personal_weight *= 0.8
    elif chip == "Party":
        accepted_weight *= 1.2
    elif chip == "Energy":
        source_weight *= 1.05
    elif chip == "Training":
        accepted_weight *= 1.1
        exploration *= 0.7

    return AutoplaySettings(
        model=str(values.get("model") or values.get("autoplay_model") or "discogs_multi"),
        visible_buffer=_bounded_int(
            visible_buffer if visible_buffer is not None else values.get("visible_buffer", values.get("autoplay_visible_buffer")),
            default=5,
            minimum=1,
            maximum=50,
        ),
        candidate_count=_bounded_int(
            candidate_count if candidate_count is not None else values.get("candidate_count", values.get("autoplay_candidate_count")),
            default=200,
            minimum=1,
            maximum=500,
        ),
        source_weight=source_weight,
        accepted_weight=accepted_weight,
        personal_weight=personal_weight,
        exploration_ratio=exploration,
        max_per_artist=_bounded_int(values.get("max_per_artist", values.get("autoplay_max_per_artist")), default=2, minimum=1, maximum=20),
        max_per_release=_bounded_int(values.get("max_per_release", values.get("autoplay_max_per_release")), default=1, minimum=1, maximum=20),
        recent_skip_penalty=_bounded_float(
            values.get("recent_skip_penalty", values.get("autoplay_recent_skip_penalty")),
            default=0.45,
            minimum=0.0,
            maximum=2.0,
        ),
        preference_chip=chip,
    )


def build_source_context(store: Store, session: PlaybackSession) -> SourceContext:
    items = store.list_queue_items(session.id, include_removed=True)
    events = store.list_playback_events(session.id)
    accepted_ids = _event_track_ids(events, POSITIVE_EVENTS)
    skipped_ids = _event_track_ids(events, NEGATIVE_EVENTS)
    played_ids = [item.track_id for item in items if item.status in {"played", "skipped"}]
    current_ids = [session.current_track_id] if session.current_track_id else []
    existing_queue_ids = [item.track_id for item in items if item.status != "removed"]
    source_ids, source_debug = _source_seed_track_ids(store, session, items)
    seed_ids = _dedupe([*accepted_ids, *source_ids])
    exclude_ids = set(
        int(track_id)
        for track_id in [*existing_queue_ids, *played_ids, *current_ids, *skipped_ids]
        if track_id
    )
    return SourceContext(
        source_type=session.source_type,
        source_id=session.source_id,
        source_track_ids=source_ids,
        seed_track_ids=seed_ids,
        accepted_track_ids=_dedupe(accepted_ids),
        skipped_track_ids=_dedupe(skipped_ids),
        played_track_ids=_dedupe(played_ids),
        existing_queue_track_ids=_dedupe(existing_queue_ids),
        current_track_ids=_dedupe(current_ids),
        exclude_track_ids=exclude_ids,
        source_debug=source_debug,
    )


def generate_autoplay_candidates(
    store: Store,
    settings: Settings,
    session: PlaybackSession,
    autoplay_settings: AutoplaySettings,
) -> tuple[list[AutoplayCandidate], SourceContext, dict[str, object]]:
    context = build_source_context(store, session)
    seed_tracks = [track for track in store.get_tracks(context.seed_track_ids).values() if track.missing_at is None]
    seed_tracks = [track for track in seed_tracks if store.load_embedding(track.id, autoplay_settings.model) is not None]
    debug: dict[str, object] = {
        "source_type": context.source_type,
        "source_id": context.source_id,
        "source_track_ids": context.source_track_ids,
        "seed_track_ids": [track.id for track in seed_tracks],
        "source_debug": context.source_debug,
        "accepted_track_ids": context.accepted_track_ids,
        "skipped_track_ids": context.skipped_track_ids,
        "excluded_counts": {
            "existing_queue": len(context.existing_queue_track_ids),
            "played": len(context.played_track_ids),
            "current": len(context.current_track_ids),
            "skipped": len(context.skipped_track_ids),
            "unique_total": len(context.exclude_track_ids),
        },
        "unavailable_candidate_count": 0,
    }
    if not seed_tracks:
        debug["empty_reason"] = "no_seed_embeddings"
        return [], context, debug

    raw_count = max(autoplay_settings.candidate_count, autoplay_settings.visible_buffer * 8)
    similar, skipped_seed_ids = Recommender(store, settings, autoplay_settings.model).similar_mix(
        seed_tracks,
        k=raw_count,
        max_per_artist=max(autoplay_settings.candidate_count, 50),
        exclude_same_album=False,
        candidate_multiplier=5,
    )
    debug["skipped_seed_ids"] = skipped_seed_ids
    accepted_vectors = _load_vectors(store, context.accepted_track_ids, autoplay_settings.model)
    skipped_vectors = _load_vectors(store, context.skipped_track_ids, autoplay_settings.model)
    scored: list[AutoplayCandidate] = []
    for result in similar:
        if result.track.id in context.exclude_track_ids:
            continue
        if result.track.missing_at is not None:
            debug["unavailable_candidate_count"] = int(debug["unavailable_candidate_count"]) + 1
            continue
        candidate_vector = store.load_embedding(result.track.id, autoplay_settings.model)
        if candidate_vector is None:
            continue
        accepted_similarity = _max_similarity(candidate_vector, accepted_vectors)
        skip_similarity = _max_similarity(candidate_vector, skipped_vectors)
        preference = store.get_track_preference(result.track.id)
        personal_score = _personal_score(preference.score if preference else 0.0)
        freshness_bonus = 0.03 if preference is None or not preference.last_played_at else 0.0
        continuity_bonus = _continuity_bonus(store, session, result.track.id)
        skip_penalty = autoplay_settings.recent_skip_penalty * max(0.0, skip_similarity)
        already_played_penalty = 0.0
        artist_fatigue_penalty = 0.0
        release_fatigue_penalty = 0.0
        unavailable_penalty = 0.0
        score = (
            autoplay_settings.source_weight * result.similarity
            + autoplay_settings.accepted_weight * accepted_similarity
            + autoplay_settings.personal_weight * personal_score
            + autoplay_settings.exploration_ratio * freshness_bonus
            + continuity_bonus
            - already_played_penalty
            - skip_penalty
            - artist_fatigue_penalty
            - release_fatigue_penalty
            - unavailable_penalty
        )
        reason = _candidate_reason(session, result.similarity, accepted_similarity, personal_score)
        scored.append(
            AutoplayCandidate(
                track=result.track,
                source_similarity=result.similarity,
                accepted_similarity=accepted_similarity,
                personal_score=personal_score,
                score=score,
                reason=reason,
                debug={
                    "score_breakdown": {
                        "source_similarity": result.similarity,
                        "accepted_session_similarity": accepted_similarity,
                        "personal_preference": personal_score,
                        "freshness_bonus": freshness_bonus,
                        "continuity_bonus": continuity_bonus,
                        "already_played_penalty": already_played_penalty,
                        "recent_skip_penalty": skip_penalty,
                        "artist_fatigue_penalty": artist_fatigue_penalty,
                        "release_fatigue_penalty": release_fatigue_penalty,
                        "unavailable_penalty": unavailable_penalty,
                    },
                    "settings": {
                        "source_weight": autoplay_settings.source_weight,
                        "accepted_weight": autoplay_settings.accepted_weight,
                        "personal_weight": autoplay_settings.personal_weight,
                        "exploration_ratio": autoplay_settings.exploration_ratio,
                        "preference_chip": autoplay_settings.preference_chip,
                    },
                },
            )
        )
    scored.sort(key=lambda item: item.score, reverse=True)
    capped, cap_debug = apply_autoplay_caps(store, scored, session, autoplay_settings)
    debug.update(cap_debug)
    debug["candidate_count"] = len(capped)
    return capped, context, debug


def apply_autoplay_caps(
    store: Store,
    candidates: list[AutoplayCandidate],
    session: PlaybackSession,
    settings: AutoplaySettings,
) -> tuple[list[AutoplayCandidate], dict[str, object]]:
    artist_counts: dict[int, int] = {}
    release_counts: dict[int, int] = {}
    for item in store.list_queue_items(session.id):
        if item.origin != "autoplay" or item.status == "removed":
            continue
        for artist_id in store.artist_ids_for_track(item.track_id):
            artist_counts[artist_id] = artist_counts.get(artist_id, 0) + 1
        release_id = store.release_id_for_track(item.track_id)
        if release_id is not None:
            release_counts[release_id] = release_counts.get(release_id, 0) + 1

    kept: list[AutoplayCandidate] = []
    skipped_artist_cap = 0
    skipped_release_cap = 0
    for candidate in candidates:
        artist_ids = store.artist_ids_for_track(candidate.track.id)
        release_id = store.release_id_for_track(candidate.track.id)
        if artist_ids and any(artist_counts.get(artist_id, 0) >= settings.max_per_artist for artist_id in artist_ids):
            skipped_artist_cap += 1
            continue
        if release_id is not None and release_counts.get(release_id, 0) >= settings.max_per_release:
            skipped_release_cap += 1
            continue
        kept.append(candidate)
        for artist_id in artist_ids:
            artist_counts[artist_id] = artist_counts.get(artist_id, 0) + 1
        if release_id is not None:
            release_counts[release_id] = release_counts.get(release_id, 0) + 1
    return kept, {
        "skipped_artist_cap": skipped_artist_cap,
        "skipped_release_cap": skipped_release_cap,
    }


def refill_autoplay_queue(
    store: Store,
    settings: Settings,
    session: PlaybackSession,
    request_settings: dict[str, object] | None = None,
    *,
    visible_buffer: int | None = None,
    candidate_count: int | None = None,
    include_debug: bool = False,
) -> AutoplayRefillResult:
    autoplay_settings = resolve_autoplay_settings(
        session,
        request_settings,
        visible_buffer=visible_buffer,
        candidate_count=candidate_count,
    )
    state = _session_state(session)
    needed = autoplay_items_needed(store, session, autoplay_settings.visible_buffer)
    pool = _valid_autoplay_pool_items(store, session, state)
    debug: dict[str, object] = {
        "needed": needed,
        "pool_before": len(pool),
        "pool_target": autoplay_settings.candidate_count,
    }
    pool_low_watermark = autoplay_pool_low_watermark(autoplay_settings)
    debug["pool_low_watermark"] = pool_low_watermark
    generated_pool: list[dict[str, object]] = []
    should_generate_pool = len(pool) < needed or len(pool) < pool_low_watermark
    debug["generated_pool_requested"] = should_generate_pool
    if should_generate_pool:
        candidates, _context, candidate_debug = generate_autoplay_candidates(
            store,
            settings,
            session,
            autoplay_settings,
        )
        generated_pool = [
            _candidate_pool_item(candidate, session)
            for candidate in candidates
            if candidate.track.id not in {int(item["track_id"]) for item in pool}
        ]
        debug.update(candidate_debug)
    pool = _dedupe_pool_items([*pool, *generated_pool])[: autoplay_settings.candidate_count]
    items = [_queue_item_from_pool_item(item) for item in pool[:needed]]
    remaining_pool = pool[needed:]
    before_ids = {item.id for item in store.list_queue_items(session.id)}
    queue = store.append_queue_items(session.id, items)
    added = [item for item in queue if item.id not in before_ids]
    state[AUTOPLAY_POOL_STATE_KEY] = remaining_pool
    store.update_playback_session(session.id, state=state)
    debug["added_count"] = len(added)
    debug["pool_after"] = len(remaining_pool)
    debug["generated_pool_count"] = len(generated_pool)
    return AutoplayRefillResult(
        session_id=session.id,
        added_items=added,
        candidate_count=autoplay_settings.candidate_count,
        debug=debug if include_debug else None,
    )


def autoplay_pool_low_watermark(settings: AutoplaySettings) -> int:
    return min(
        settings.candidate_count,
        max(settings.visible_buffer * 2, math.ceil(settings.candidate_count * AUTOPLAY_POOL_LOW_WATERMARK_RATIO)),
    )


def autoplay_items_needed(store: Store, session: PlaybackSession, visible_buffer: int) -> int:
    items = store.list_queue_items(session.id)
    current_index = 0
    if session.current_queue_item_id:
        for index, item in enumerate(items):
            if item.id == session.current_queue_item_id:
                current_index = index
                break
    upcoming = [item for item in items[current_index + 1 :] if item.status == "queued"]
    return max(0, visible_buffer - len(upcoming))


def _source_seed_track_ids(
    store: Store,
    session: PlaybackSession,
    items: list[QueueItem],
) -> tuple[list[int], dict[str, object]]:
    if session.source_type == "track" and session.source_id is not None:
        return [session.source_id], {"strategy": "track_seed"}
    if session.source_type == "release" and session.source_id is not None:
        release_track_ids = [item.track.id for item in store.list_release_tracks(session.source_id)]
        return release_track_ids, {"strategy": "release_tracks", "release_track_count": len(release_track_ids)}
    if session.source_type == "artist" and session.source_id is not None:
        artist_track_ids = store.list_artist_track_ids(session.source_id, limit=50)
        return artist_track_ids, {"strategy": "artist_tracks", "artist_track_count": len(artist_track_ids)}
    if session.source_type == "playlist" and session.source_id is not None:
        playlist_track_ids = [item.track_id for item in store.list_playlist_items(session.source_id)]
        return playlist_track_ids, {"strategy": "playlist_items", "playlist_track_count": len(playlist_track_ids)}
    if session.source_type == "generated_mix":
        mix_id = _generated_mix_id(session)
        if mix_id:
            mix_track_ids = [item.track_id for item in store.list_generated_mix_items(mix_id)]
            return mix_track_ids, {
                "strategy": "generated_mix_items",
                "generated_mix_id": mix_id,
                "generated_mix_track_count": len(mix_track_ids),
            }
    queue_track_ids = [
        item.track_id
        for item in items
        if item.origin in {"source", "manual", "generated_mix", "flow"} and item.status != "removed"
    ]
    strategy = "accepted_or_source_queue"
    if session.source_type == "search":
        strategy = "search_result_queue"
    elif session.source_type == "manual":
        strategy = "manual_queue"
    elif session.source_type == "flow":
        strategy = "flow_queue"
    elif session.source_type == "playlist":
        strategy = "playlist_queue"
    elif session.source_type == "generated_mix":
        strategy = "generated_mix_queue"
    return queue_track_ids, {"strategy": strategy, "queue_track_count": len(queue_track_ids)}


def _candidate_pool_item(candidate: AutoplayCandidate, session: PlaybackSession) -> dict[str, object]:
    return {
        "track_id": candidate.track.id,
        "origin": "autoplay",
        "source_type": session.source_type,
        "source_id": session.source_id,
        "reason": candidate.reason,
        "score": candidate.score,
        "debug": candidate.debug,
    }


def _queue_item_from_pool_item(item: dict[str, object]) -> dict[str, object]:
    return {
        "track_id": int(item["track_id"]),
        "origin": "autoplay",
        "source_type": item.get("source_type"),
        "source_id": item.get("source_id"),
        "reason": item.get("reason"),
        "score": item.get("score"),
        "debug": item.get("debug") if isinstance(item.get("debug"), dict) else None,
    }


def _valid_autoplay_pool_items(
    store: Store,
    session: PlaybackSession,
    state: dict[str, object],
) -> list[dict[str, object]]:
    raw_pool = state.get(AUTOPLAY_POOL_STATE_KEY)
    if not isinstance(raw_pool, list):
        return []
    excluded = {item.track_id for item in store.list_queue_items(session.id) if item.status != "removed"}
    valid: list[dict[str, object]] = []
    for raw_item in raw_pool:
        if not isinstance(raw_item, dict):
            continue
        try:
            track_id = int(raw_item["track_id"])
        except (KeyError, TypeError, ValueError):
            continue
        track = store.get_track(track_id)
        if track_id in excluded or track is None or track.missing_at is not None:
            continue
        item = dict(raw_item)
        item["track_id"] = track_id
        valid.append(item)
    return _dedupe_pool_items(valid)


def _dedupe_pool_items(items: list[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    seen: set[int] = set()
    for item in items:
        try:
            track_id = int(item["track_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if track_id in seen:
            continue
        seen.add(track_id)
        result.append(item)
    return result


def _event_track_ids(events: Iterable[PlaybackEvent], event_types: set[str]) -> list[int]:
    return _dedupe(
        int(event.track_id)
        for event in events
        if event.track_id is not None and event.event_type in event_types
    )


def _load_vectors(store: Store, track_ids: list[int], model_name: str) -> list[np.ndarray]:
    vectors: list[np.ndarray] = []
    for track_id in track_ids:
        vector = store.load_embedding(track_id, model_name)
        if vector is not None:
            vectors.append(vector)
    return vectors


def _max_similarity(vector: np.ndarray, others: list[np.ndarray]) -> float:
    if not others:
        return 0.0
    return max(_cosine(vector, other) for other in others)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom <= 0.0 or not math.isfinite(denom):
        return 0.0
    return float(np.dot(a, b) / denom)


def _personal_score(raw_score: float) -> float:
    return max(-1.0, min(1.0, raw_score / 10.0))


def _continuity_bonus(store: Store, session: PlaybackSession, track_id: int) -> float:
    if session.source_type == "release" and session.source_id is not None:
        return 0.05 if store.release_id_for_track(track_id) == session.source_id else 0.0
    if session.source_type == "artist" and session.source_id is not None:
        return 0.08 if session.source_id in store.artist_ids_for_track(track_id) else 0.0
    return 0.0


def _candidate_reason(
    session: PlaybackSession,
    source_similarity: float,
    accepted_similarity: float,
    personal_score: float,
) -> str:
    if accepted_similarity >= source_similarity and accepted_similarity > 0:
        return "Because it follows tracks accepted in this session"
    if personal_score > 0.4:
        return "Because it matches this source and your listening history"
    label = session.source_label or session.source_type
    return f"Because it continues {label}"


def _session_settings(session: PlaybackSession) -> dict[str, object]:
    if not session.settings_json:
        return {}
    try:
        import json

        decoded = json.loads(session.settings_json)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _session_state(session: PlaybackSession) -> dict[str, object]:
    if not session.state_json:
        return {}
    try:
        import json

        decoded = json.loads(session.state_json)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _generated_mix_id(session: PlaybackSession) -> str | None:
    settings = _session_settings(session)
    state = _session_state(session)
    for values in (settings, state):
        raw = values.get("generated_mix_id") or values.get("mix_id")
        if raw:
            return str(raw)
    return None


def _dedupe(values: Iterable[int]) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        parsed = int(value)
        if parsed in seen:
            continue
        seen.add(parsed)
        result.append(parsed)
    return result


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _bounded_float(value: object, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))
