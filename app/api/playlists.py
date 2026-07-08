"""Playlist API routes (liked tracks, etc.)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.api.deps import _navidrome_client, api_error, context, playback_session_settings
from app.navidrome_starred import build_starred_track_ids
from app.schemas.responses import PlaybackSessionEnvelopeResponse
from app.serializers.playback import playback_session_response
from app.serializers.entities import track_summary_dict

router = APIRouter(prefix="/api/v1")


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
