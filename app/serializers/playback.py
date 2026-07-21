"""Playback serializers: session, queue, events, scrobble logic.

Extracted from app/main.py.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.models import Artist, Track
from app.navidrome import NavidromeClient
from app.schemas.requests import PlaybackQueuePatchRequest, PlaybackSessionCreateRequest
from app.serializers.entities import _json_object, track_summary_dict
from app.store import Store, playback_event_is_completion

logger = logging.getLogger(__name__)
navidrome_logger = logging.getLogger("discocs.navidrome")


# ---------------------------------------------------------------------------
# Session / queue
# ---------------------------------------------------------------------------

def playback_session_dict(store: Store, session) -> dict[str, object]:
    current_track = store.get_track(session.current_track_id) if session.current_track_id else None
    return {
        "id": session.id,
        "source_type": session.source_type,
        "source_id": session.source_id,
        "source_label": session.source_label,
        "mode": session.mode,
        "status": session.status,
        "current_track_id": session.current_track_id,
        "current_queue_item_id": session.current_queue_item_id,
        "current_track": track_summary_dict(store, current_track) if current_track else None,
        "autoplay_enabled": session.autoplay_enabled,
        "shuffle_enabled": session.shuffle_enabled,
        "repeat_mode": session.repeat_mode,
        "started_at": session.started_at,
        "updated_at": session.updated_at,
        "ended_at": session.ended_at,
        "settings": _json_object(session.settings_json),
        "state": _json_object(session.state_json),
    }


def queue_item_dict(
    store: Store,
    item,
    include_debug: bool = False,
    artists_by_track: dict[int, list[Artist]] | None = None,
) -> dict[str, object]:
    track = store.get_track(item.track_id)
    artists = artists_by_track.get(item.track_id, []) if artists_by_track is not None else []
    data: dict[str, object] = {
        "id": item.id,
        "session_id": item.session_id,
        "track_id": item.track_id,
        "track": track_summary_dict(store, track, artists) if track else None,
        "position": item.position,
        "origin": item.origin,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "status": item.status,
        "locked": item.locked,
        "reason": item.reason,
        "score": item.score,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
    if include_debug:
        data["debug"] = _json_object(item.debug_json)
    return data


def playback_queue_dict(store: Store, session, items, include_debug: bool = False) -> dict[str, object]:
    artists_by_track = store.artists_for_tracks([item.track_id for item in items])
    current_index = 0
    if session.current_queue_item_id:
        for index, item in enumerate(items):
            if item.id == session.current_queue_item_id:
                current_index = index
                break
    current_item = items[current_index] if items else None
    return {
        "items": [
            queue_item_dict(store, item, include_debug=include_debug, artists_by_track=artists_by_track)
            for item in items
        ],
        "current_index": current_index,
        "current_item": (
            queue_item_dict(store, current_item, include_debug=include_debug, artists_by_track=artists_by_track)
            if current_item
            else None
        ),
        "upcoming": [
            queue_item_dict(store, item, include_debug=include_debug, artists_by_track=artists_by_track)
            for item in items[current_index + 1:]
        ],
        "played": [
            queue_item_dict(store, item, include_debug=include_debug, artists_by_track=artists_by_track)
            for item in items
            if item.status in {"played", "skipped"}
        ],
        "source_items": [
            queue_item_dict(store, item, include_debug=include_debug, artists_by_track=artists_by_track)
            for item in items
            if item.origin == "source"
        ],
        "generated_items": [
            queue_item_dict(store, item, include_debug=include_debug, artists_by_track=artists_by_track)
            for item in items
            if item.origin in {"autoplay", "flow", "generated_mix"}
        ],
        "autoplay_pool": autoplay_pool_dict(
            store,
            session,
            include_debug=include_debug,
            exclude_track_ids={item.track_id for item in items if item.status != "removed"},
        ),
    }


def autoplay_pool_dict(
    store: Store,
    session,
    include_debug: bool = False,
    exclude_track_ids: set[int] | None = None,
) -> list[dict[str, object]]:
    # Tracks already promoted into the queue must not linger in the autoplay
    # pool — otherwise jumping to a pool item (which copies every preceding pool
    # track into the queue) leaves duplicates showing under "Автовоспроизведение".
    excluded = exclude_track_ids or set()
    state = _json_object(session.state_json)
    raw_pool = state.get("autoplay_pool")
    if not isinstance(raw_pool, list):
        return []
    track_ids: list[int] = []
    for raw_item in raw_pool:
        if not isinstance(raw_item, dict):
            continue
        try:
            track_ids.append(int(raw_item["track_id"]))
        except (KeyError, TypeError, ValueError):
            continue
    artists_by_track = store.artists_for_tracks(track_ids)
    result: list[dict[str, object]] = []
    for index, raw_item in enumerate(raw_pool):
        if not isinstance(raw_item, dict):
            continue
        try:
            track_id = int(raw_item["track_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if track_id in excluded:
            continue
        track = store.get_track(track_id)
        if track is None:
            continue
        item: dict[str, object] = {
            "id": f"autoplay-pool-{track_id}",
            "session_id": session.id,
            "track_id": track_id,
            "track": track_summary_dict(store, track, artists_by_track.get(track_id, [])),
            "position": index,
            "origin": "autoplay_pool",
            "source_type": raw_item.get("source_type"),
            "source_id": raw_item.get("source_id"),
            "status": "prepared",
            "locked": False,
            "reason": raw_item.get("reason"),
            "score": raw_item.get("score"),
        }
        if include_debug and isinstance(raw_item.get("debug"), dict):
            item["debug"] = raw_item["debug"]
        result.append(item)
    return result


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def playback_event_dict(event) -> dict[str, object]:
    return {
        "id": event.id,
        "session_id": event.session_id,
        "queue_item_id": event.queue_item_id,
        "track_id": event.track_id,
        "release_id": event.release_id,
        "artist_id": event.artist_id,
        "event_type": event.event_type,
        "position_seconds": event.position_seconds,
        "duration_seconds": event.duration_seconds,
        "play_fraction": event.play_fraction,
        "created_at": event.created_at,
        "client_event_id": event.client_event_id,
        "source": event.source,
        "payload": _json_object(event.payload_json),
    }


def playback_event_time_ms(created_at: str) -> int | None:
    try:
        value = datetime.fromisoformat(created_at)
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1000)


# ---------------------------------------------------------------------------
# Scrobble helpers
# ---------------------------------------------------------------------------

def should_scrobble_navidrome_play(store: Store, result) -> bool:
    event = result.event
    if result.duplicate or event.track_id is None:
        return False
    if event.event_type == "play_threshold_reached":
        return True
    if event.event_type != "completed" or not playback_event_is_completion(
        event.position_seconds,
        event.duration_seconds,
        event.play_fraction,
    ):
        return False
    if not event.session_id:
        return True
    for prior in store.list_playback_events(event.session_id):
        if prior.id == event.id:
            continue
        if prior.event_type not in {"play_threshold_reached", "completed"}:
            continue
        same_queue_item = event.queue_item_id and prior.queue_item_id == event.queue_item_id
        same_track_without_queue = not event.queue_item_id and prior.track_id == event.track_id
        if same_queue_item or same_track_without_queue:
            return False
    return True


def navidrome_scrobble_submission(store: Store, result) -> tuple[bool, str] | None:
    event = result.event
    if result.duplicate:
        return None
    if event.track_id is None:
        return None
    if event.event_type == "track_started":
        return (False, "now_playing")
    if should_scrobble_navidrome_play(store, result):
        return (True, "submission")
    return None


def maybe_scrobble_navidrome_play(store: Store, settings, result) -> dict[str, object]:
    decision = navidrome_scrobble_submission(store, result)
    if decision is None:
        return {"status": "skipped", "reason": "event_not_scrobbleable"}
    submission, mode = decision
    track_id = result.event.track_id
    if track_id is None:
        return {"status": "skipped", "reason": "missing_track_id"}
    item_id = store.external_id_for_track("navidrome", track_id)
    if not item_id:
        return {"status": "skipped", "reason": "no_navidrome_mapping", "track_id": track_id}
    from dataclasses import replace
    from app.user_context import current_navidrome_credentials

    credentials = current_navidrome_credentials()
    if credentials is None:
        if settings.auth.enabled:
            return {"status": "skipped", "reason": "missing_user_credentials", "track_id": track_id}
        nav = settings.navidrome
    else:
        nav = replace(
            settings.navidrome,
            user=credentials.username,
            password=credentials.password,
            auth_mode="token",
        )
    try:
        NavidromeClient(nav).scrobble_song(
            item_id,
            played_at_ms=playback_event_time_ms(result.event.created_at),
            submission=submission,
        )
    except Exception as exc:
        navidrome_logger.warning(
            "Navidrome scrobble failed track_id=%s item_id=%s event_id=%s error=%s",
            track_id, item_id, result.event.id, exc,
        )
        return {"status": "failed", "mode": mode, "track_id": track_id, "item_id": item_id, "error": str(exc)}
    return {"status": "ok", "mode": mode, "track_id": track_id, "item_id": item_id, "submission": submission}


# ---------------------------------------------------------------------------
# Composite responses
# ---------------------------------------------------------------------------

def playback_session_response(store: Store, session, include_debug: bool = False) -> dict[str, object]:
    queue_items = store.list_queue_items(session.id)
    return {
        "session": playback_session_dict(store, session),
        "queue": playback_queue_dict(store, session, queue_items, include_debug=include_debug),
    }


def build_initial_playback_queue(store: Store, request: PlaybackSessionCreateRequest) -> list[int]:
    if request.track_ids:
        return request.track_ids
    if request.track_id is not None:
        return [request.track_id]
    if request.source_type == "track":
        if request.source_id is None:
            raise ValueError("source_id is required for track playback sessions")
        return [request.source_id]
    if request.source_type == "release":
        if request.source_id is None:
            raise ValueError("source_id is required for release playback sessions")
        tracks = store.list_release_tracks(request.source_id)
        return [item.track.id for item in tracks]
    if request.source_type == "artist":
        if request.source_id is None:
            raise ValueError("source_id is required for artist playback sessions")
        with store.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT t.id
                FROM track_artists ta
                JOIN tracks t ON t.id = ta.track_id
                LEFT JOIN release_tracks rt ON rt.track_id = t.id
                WHERE ta.artist_id = ? AND ta.role = 'primary'
                ORDER BY COALESCE(rt.position, t.id), t.id
                LIMIT 100
                """,
                (request.source_id,),
            ).fetchall()
        return [int(row["id"]) for row in rows]
    return []


def queue_patch_items(request: PlaybackQueuePatchRequest) -> list[dict[str, object]]:
    if request.items:
        return [item.model_dump(exclude_none=True) for item in request.items]
    if request.track_ids:
        return [{"track_id": track_id, "origin": "manual"} for track_id in request.track_ids]
    if request.track_id is not None:
        return [{"track_id": request.track_id, "origin": "manual"}]
    return []
