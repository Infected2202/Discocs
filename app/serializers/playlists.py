"""Serializers for user playlists."""
from __future__ import annotations

from app.models import Playlist
from app.serializers.entities import _json_object, image_ref, track_summary_dict
from app.store import Store


def _playlist_artwork(store: Store, playlist: Playlist) -> dict[str, object]:
    if playlist.cover_path:
        return image_ref(f"/api/v1/playlists/{playlist.id}/cover", "playlist")
    track_ids = store.playlist_track_ids(playlist.id)
    if track_ids:
        return image_ref(f"/api/v1/tracks/{track_ids[0]}/cover?size=512", "track")
    return image_ref(None, "none")


def playlist_summary_dict(
    store: Store,
    playlist: Playlist,
    track_count: int | None = None,
) -> dict[str, object]:
    if track_count is None:
        track_count = store.playlist_track_counts([playlist.id]).get(playlist.id, 0)
    source = _json_object(playlist.source_json)
    visibility = "public" if source and source.get("visibility") == "public" else "private"
    return {
        "id": playlist.id,
        "title": playlist.title,
        "kind": playlist.kind,
        "description": playlist.description,
        "track_count": track_count,
        "artwork": _playlist_artwork(store, playlist),
        "source": source,
        "visibility": visibility,
        "editable": playlist.user_id == store.user_id,
        "created_at": playlist.created_at,
        "updated_at": playlist.updated_at,
        "action": {"type": "open", "target": f"/playlists/{playlist.id}"},
        "play_action": {"type": "post", "endpoint": f"/api/v1/playlists/{playlist.id}/play"},
    }


def playlist_detail_dict(store: Store, playlist: Playlist) -> dict[str, object]:
    track_ids = store.playlist_track_ids(playlist.id)
    tracks = [
        track
        for track_id in track_ids
        if (track := store.get_track(track_id)) is not None and track.missing_at is None
    ]
    artists_by_track = store.artists_for_tracks([track.id for track in tracks])
    detail = playlist_summary_dict(store, playlist, track_count=len(tracks))
    detail["tracks"] = [
        track_summary_dict(store, track, artists_by_track.get(track.id, []))
        for track in tracks
    ]
    return detail
