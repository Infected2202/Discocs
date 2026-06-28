"""Playback and autoplay API routes.

Extracted from app/main.py — Stage 6c.
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.api.deps import (
    api_error,
    context,
    playback_session_settings,
    playback_settings_defaults,
    request_field_names,
)
from app.autoplay import refill_autoplay_queue
from app.schemas.requests import (
    AutoplayRefillRequest,
    PlaybackEventRequest,
    PlaybackQueuePatchRequest,
    PlaybackSessionCreateRequest,
    PlaybackSessionPatchRequest,
)
from app.schemas.responses import (
    AutoplayRefillResponse,
    PlaybackEventIngestResponse,
    PlaybackSessionEnvelopeResponse,
    PlaybackSettingsResponse,
)
from app.serializers.playback import (
    build_initial_playback_queue,
    maybe_scrobble_navidrome_play,
    playback_event_dict,
    playback_queue_dict,
    playback_session_dict,
    playback_session_response,
    queue_item_dict,
    queue_patch_items,
)

router = APIRouter()


@router.get("/api/v1/playback/settings", response_model=PlaybackSettingsResponse)
def api_v1_playback_settings() -> dict[str, object]:
    return {"settings": playback_settings_defaults()}


@router.post("/api/v1/autoplay/refill", response_model=AutoplayRefillResponse)
def api_v1_autoplay_refill(
    request: AutoplayRefillRequest,
    include_debug: bool = Query(False),
) -> dict[str, object] | JSONResponse:
    store, settings = context()
    session = store.get_playback_session(request.session_id)
    if session is None:
        return api_error(404, "not_found", "Playback session not found")
    if not session.autoplay_enabled:
        return {
            "session_id": session.id,
            "added_items": [],
            "candidate_count": request.candidate_count or 0,
            "debug": {"autoplay_enabled": False} if include_debug else None,
        }
    try:
        result = refill_autoplay_queue(
            store,
            settings,
            session,
            request.settings,
            visible_buffer=request.visible_buffer,
            candidate_count=request.candidate_count,
            include_debug=include_debug,
        )
    except FileNotFoundError as exc:
        return api_error(409, "index_not_ready", str(exc))
    except LookupError as exc:
        return api_error(404, "not_found", str(exc))
    return {
        "session_id": result.session_id,
        "added_items": [
            queue_item_dict(store, item, include_debug=include_debug)
            for item in result.added_items
        ],
        "candidate_count": result.candidate_count,
        "debug": result.debug,
    }


@router.post("/api/v1/playback/sessions", response_model=PlaybackSessionEnvelopeResponse)
def api_v1_create_playback_session(request: PlaybackSessionCreateRequest) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    try:
        track_ids = build_initial_playback_queue(store, request)
        if request.source_type in {"track", "release", "artist"} and not track_ids:
            return api_error(404, "not_found", "Playback source has no local tracks")
        session, _queue = store.create_playback_session(
            source_type=request.source_type,
            source_id=request.source_id,
            source_label=request.source_label,
            mode=request.mode,
            track_ids=track_ids,
            autoplay_enabled=request.autoplay_enabled,
            shuffle_enabled=request.shuffle_enabled,
            repeat_mode=request.repeat_mode,
            settings=playback_session_settings(request.settings),
            state=request.state,
        )
    except ValueError as exc:
        return api_error(400, "invalid_request", str(exc))
    return playback_session_response(store, session)


@router.get("/api/v1/playback/sessions/{session_id}", response_model=PlaybackSessionEnvelopeResponse)
def api_v1_get_playback_session(session_id: str) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    session = store.get_playback_session(session_id)
    if session is None:
        return api_error(404, "not_found", "Playback session not found")
    return playback_session_response(store, session)


@router.patch("/api/v1/playback/sessions/{session_id}", response_model=PlaybackSessionEnvelopeResponse)
def api_v1_update_playback_session(
    session_id: str,
    request: PlaybackSessionPatchRequest,
) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    fields = request_field_names(request)
    try:
        session = store.update_playback_session(
            session_id,
            status=request.status,
            current_track_id=request.current_track_id,
            current_queue_item_id=request.current_queue_item_id,
            clear_current_track="current_track_id" in fields and request.current_track_id is None,
            clear_current_queue_item="current_queue_item_id" in fields and request.current_queue_item_id is None,
            autoplay_enabled=request.autoplay_enabled,
            shuffle_enabled=request.shuffle_enabled,
            repeat_mode=request.repeat_mode,
            settings=request.settings,
            state=request.state,
        )
    except ValueError as exc:
        return api_error(400, "invalid_request", str(exc))
    if session is None:
        return api_error(404, "not_found", "Playback session not found")
    return playback_session_response(store, session)


@router.get("/api/v1/playback/sessions/{session_id}/queue", response_model=PlaybackSessionEnvelopeResponse)
def api_v1_get_playback_queue(
    session_id: str,
    include_debug: bool = False,
) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    session = store.get_playback_session(session_id)
    if session is None:
        return api_error(404, "not_found", "Playback session not found")
    items = store.list_queue_items(session_id)
    return {"session": playback_session_dict(store, session), "queue": playback_queue_dict(store, session, items, include_debug)}


@router.patch("/api/v1/playback/sessions/{session_id}/queue", response_model=PlaybackSessionEnvelopeResponse)
def api_v1_patch_playback_queue(
    session_id: str,
    request: PlaybackQueuePatchRequest,
) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    session = store.get_playback_session(session_id)
    if session is None:
        return api_error(404, "not_found", "Playback session not found")
    try:
        if request.operation == "replace":
            store.replace_queue_items(session_id, queue_patch_items(request))
        elif request.operation == "add":
            items = queue_patch_items(request)
            if not items:
                return api_error(400, "invalid_request", "add requires track_id, track_ids, or items")
            store.append_queue_items(session_id, items)
        elif request.operation == "remove":
            if not request.queue_item_id:
                return api_error(400, "invalid_request", "remove requires queue_item_id")
            item = store.remove_queue_item(session_id, request.queue_item_id)
            if item is None:
                return api_error(404, "not_found", "Queue item not found")
            store.record_playback_event(
                session_id=session_id,
                queue_item_id=request.queue_item_id,
                track_id=item.track_id,
                event_type="removed_from_queue",
                source="api",
            )
        elif request.operation == "move":
            if not request.queue_item_id or request.position is None:
                return api_error(400, "invalid_request", "move requires queue_item_id and position")
            store.move_queue_item(session_id, request.queue_item_id, request.position)
        elif request.operation in {"jump", "mark_current"}:
            if not request.queue_item_id:
                return api_error(400, "invalid_request", f"{request.operation} requires queue_item_id")
            item = store.jump_to_queue_item(session_id, request.queue_item_id)
            if item is None:
                return api_error(404, "not_found", "Queue item not found")
            event_type = "queue_click" if request.operation == "jump" else "track_started"
            store.record_playback_event(
                session_id=session_id,
                queue_item_id=request.queue_item_id,
                track_id=item.track_id,
                event_type=event_type,
                source="api",
            )
        else:
            return api_error(400, "invalid_request", "Unsupported queue operation")
    except ValueError as exc:
        return api_error(400, "invalid_request", str(exc))
    session = store.get_playback_session(session_id)
    items = store.list_queue_items(session_id)
    return {"session": playback_session_dict(store, session), "queue": playback_queue_dict(store, session, items)}


@router.post("/api/v1/playback/events", response_model=PlaybackEventIngestResponse)
def api_v1_record_playback_event(request: PlaybackEventRequest) -> dict[str, object] | JSONResponse:
    store, settings = context()
    try:
        result = store.record_playback_event(
            session_id=request.session_id,
            queue_item_id=request.queue_item_id,
            track_id=request.track_id,
            release_id=request.release_id,
            artist_id=request.artist_id,
            event_type=request.event_type,
            position_seconds=request.position_seconds,
            duration_seconds=request.duration_seconds,
            play_fraction=request.play_fraction,
            client_event_id=request.client_event_id,
            source=request.source,
            payload=request.payload,
        )
    except ValueError as exc:
        return api_error(400, "invalid_request", str(exc))
    navidrome_scrobble = maybe_scrobble_navidrome_play(store, settings, result)
    return {
        "accepted": True,
        "duplicate": result.duplicate,
        "event_id": result.event.id,
        "event": playback_event_dict(result.event),
        "preference_delta": result.preference_delta,
        "navidrome_scrobble": navidrome_scrobble,
    }
