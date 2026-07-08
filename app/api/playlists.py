"""Playlist API routes: liked tracks and user playlists."""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse, JSONResponse

from app.api.deps import _navidrome_client, api_error, context, playback_session_settings
from app.mix_covers import refresh_playlist_cover
from app.navidrome_starred import build_starred_track_ids
from app.schemas.requests import (
    PlaylistCreateRequest,
    PlaylistTracksRequest,
    PlaylistUpdateRequest,
)
from app.schemas.responses import PlaybackSessionEnvelopeResponse
from app.serializers.playback import playback_session_response
from app.serializers.entities import track_summary_dict
from app.serializers.playlists import playlist_detail_dict, playlist_summary_dict

router = APIRouter(prefix="/api/v1")

_PLAYLIST_NOT_FOUND = "Playlist not found"


def _liked_track_ids(store, settings) -> list[int]:
    """Return local track IDs for Navidrome-starred tracks, preserving Navidrome order."""
    try:
        client = _navidrome_client(settings)
        data = build_starred_track_ids(store, client, user=settings.navidrome.user)
        return data["track_ids"]
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Navidrome starred failed: {exc}") from exc


@router.get(
    "/playlists/likes",
    response_model=None,
    responses={502: {"description": "Navidrome starred lookup failed"}},
)
def api_v1_likes_playlist() -> dict[str, object]:
    store, settings = context()
    track_ids = _liked_track_ids(store, settings)
    tracks = [t for tid in track_ids if (t := store.get_track(tid)) is not None and t.missing_at is None]
    artists_by_track = store.artists_for_tracks([t.id for t in tracks])
    return {
        "id": "likes",
        "title": "Liked Tracks",
        "subtitle": f"{len(tracks)} tracks",
        "track_count": len(tracks),
        "tracks": [track_summary_dict(store, t, artists_by_track.get(t.id, [])) for t in tracks],
    }


@router.post(
    "/playlists/likes/play",
    response_model=PlaybackSessionEnvelopeResponse,
    responses={502: {"description": "Navidrome starred lookup failed"}},
)
def api_v1_play_likes() -> dict[str, object] | JSONResponse:
    store, settings = context()
    track_ids = _liked_track_ids(store, settings)
    track_ids = [tid for tid in track_ids if (t := store.get_track(tid)) is not None and t.missing_at is None]
    if not track_ids:
        return api_error(409, "empty_playlist", "No liked tracks found")
    session, _queue = store.create_playback_session(
        source_type="playlist",
        source_label="Liked Tracks",
        mode="linear",
        track_ids=track_ids,
        autoplay_enabled=False,
        settings=playback_session_settings({"source_playlist_id": "likes"}),
    )
    return playback_session_response(store, session)


# ---------------------------------------------------------------------------
# User playlists
# ---------------------------------------------------------------------------

@router.get("/playlists", response_model=None)
def api_v1_list_playlists(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    store, _settings = context()
    playlists = store.list_playlists(limit=limit, offset=offset)
    counts = store.playlist_track_counts([playlist.id for playlist in playlists])
    total = store.count_playlists()
    return {
        "items": [
            playlist_summary_dict(store, playlist, counts.get(playlist.id, 0))
            for playlist in playlists
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": offset + limit if offset + limit < total else None,
    }


@router.post("/playlists", response_model=None, status_code=201)
def api_v1_create_playlist(request: PlaylistCreateRequest) -> dict[str, object] | JSONResponse:
    store, settings = context()
    source = {"visibility": request.visibility} if request.visibility else None
    try:
        playlist = store.create_playlist(
            title=request.title,
            description=request.description,
            source=source,
            track_ids=request.track_ids,
        )
    except ValueError as exc:
        return api_error(422, "invalid_request", str(exc))
    if request.track_ids:
        refresh_playlist_cover(store, settings, playlist.id)
        playlist = store.get_playlist(playlist.id) or playlist
    return playlist_summary_dict(store, playlist)


@router.get("/playlists/{playlist_id}", response_model=None)
def api_v1_playlist_detail(playlist_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    playlist = store.get_playlist(playlist_id)
    if playlist is None:
        return api_error(404, "not_found", _PLAYLIST_NOT_FOUND)
    return playlist_detail_dict(store, playlist)


@router.patch("/playlists/{playlist_id}", response_model=None)
def api_v1_update_playlist(
    playlist_id: int,
    request: PlaylistUpdateRequest,
) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    patch = request.model_dump(exclude_unset=True)
    kwargs: dict[str, object] = {}
    if "title" in patch:
        kwargs["title"] = patch["title"]
    if "description" in patch:
        kwargs["description"] = patch["description"]
    try:
        playlist = store.update_playlist(playlist_id, **kwargs)
    except ValueError as exc:
        return api_error(422, "invalid_request", str(exc))
    if playlist is None:
        return api_error(404, "not_found", _PLAYLIST_NOT_FOUND)
    return playlist_summary_dict(store, playlist)


@router.delete("/playlists/{playlist_id}", response_model=None, status_code=204)
def api_v1_delete_playlist(playlist_id: int) -> Response | JSONResponse:
    store, _settings = context()
    playlist = store.get_playlist(playlist_id)
    if playlist is None:
        return api_error(404, "not_found", _PLAYLIST_NOT_FOUND)
    if playlist.cover_path:
        Path(playlist.cover_path).unlink(missing_ok=True)
    store.delete_playlist(playlist_id)
    return Response(status_code=204)


@router.post("/playlists/{playlist_id}/tracks", response_model=None)
def api_v1_add_playlist_tracks(
    playlist_id: int,
    request: PlaylistTracksRequest,
) -> dict[str, object] | JSONResponse:
    store, settings = context()
    try:
        added = store.add_playlist_tracks(playlist_id, request.track_ids)
    except ValueError as exc:
        message = str(exc)
        if _PLAYLIST_NOT_FOUND in message:
            return api_error(404, "not_found", _PLAYLIST_NOT_FOUND)
        return api_error(422, "invalid_tracks", message)
    if added:
        refresh_playlist_cover(store, settings, playlist_id)
    counts = store.playlist_track_counts([playlist_id])
    return {"added": added, "track_count": counts.get(playlist_id, 0)}


@router.post("/playlists/{playlist_id}/tracks/remove", response_model=None)
def api_v1_remove_playlist_tracks(
    playlist_id: int,
    request: PlaylistTracksRequest,
) -> dict[str, object] | JSONResponse:
    store, settings = context()
    if store.get_playlist(playlist_id) is None:
        return api_error(404, "not_found", _PLAYLIST_NOT_FOUND)
    removed = store.remove_playlist_tracks(playlist_id, request.track_ids)
    if removed:
        refresh_playlist_cover(store, settings, playlist_id)
    counts = store.playlist_track_counts([playlist_id])
    return {"removed": removed, "track_count": counts.get(playlist_id, 0)}


@router.post("/playlists/{playlist_id}/play", response_model=PlaybackSessionEnvelopeResponse)
def api_v1_play_playlist(playlist_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    playlist = store.get_playlist(playlist_id)
    if playlist is None:
        return api_error(404, "not_found", _PLAYLIST_NOT_FOUND)
    track_ids = [
        track_id
        for track_id in store.playlist_track_ids(playlist_id)
        if (track := store.get_track(track_id)) is not None and track.missing_at is None
    ]
    if not track_ids:
        return api_error(409, "empty_playlist", "Playlist has no playable tracks")
    session, _queue = store.create_playback_session(
        source_type="playlist",
        source_id=playlist.id,
        source_label=playlist.title,
        mode="linear",
        track_ids=track_ids,
        autoplay_enabled=False,
        settings=playback_session_settings({"source_playlist_id": playlist.id}),
    )
    return playback_session_response(store, session)


@router.get("/playlists/{playlist_id}/cover", response_model=None)
def api_v1_playlist_cover(playlist_id: int) -> FileResponse | JSONResponse:
    store, _settings = context()
    playlist = store.get_playlist(playlist_id)
    if playlist is None:
        return api_error(404, "not_found", _PLAYLIST_NOT_FOUND)
    if not playlist.cover_path:
        return api_error(404, "not_found", "Playlist has no cover")
    path = Path(playlist.cover_path)
    if not path.exists() or not path.is_file():
        return api_error(404, "not_found", "Playlist cover not found")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
