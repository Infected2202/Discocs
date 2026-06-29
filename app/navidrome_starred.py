from __future__ import annotations

from typing import Any

from app.navidrome import NavidromeClient, NavidromeSong
from app.store import Store, Track

NAVIDROME_PROVIDER = "navidrome"


def build_starred_catalog(
    store: Store,
    client: NavidromeClient,
    *,
    model: str,
    user: str,
) -> dict[str, Any]:
    songs = client.get_starred_songs()
    results: list[dict[str, Any]] = []
    mapped_count = 0
    ready_count = 0
    missing_embedding_count = 0
    not_synced_count = 0

    for song in songs:
        if not song.id:
            continue
        track = store.get_track_by_external_id(NAVIDROME_PROVIDER, song.id)
        if track is None:
            status = "not_synced"
            not_synced_count += 1
            track_payload = _navidrome_song_dict(song)
        else:
            mapped_count += 1
            has_embedding = store.load_embedding(track.id, model) is not None
            if has_embedding:
                status = "ready"
                ready_count += 1
            else:
                status = "missing_embedding"
                missing_embedding_count += 1
            track_payload = _mapped_track_dict(track, has_embedding=has_embedding)
            track_payload["navidrome_item_id"] = song.id
        results.append(
            {
                "item_id": song.id,
                "status": status,
                "track": track_payload,
            }
        )

    return {
        "user": user,
        "count": len(results),
        "mapped_count": mapped_count,
        "ready_count": ready_count,
        "missing_embedding_count": missing_embedding_count,
        "not_synced_count": not_synced_count,
        "results": results,
    }


def ready_tracks_from_starred_catalog(catalog: dict[str, Any], store: Store, model: str) -> list[Track]:
    tracks: list[Track] = []
    for item in catalog.get("results", []):
        if item.get("status") != "ready":
            continue
        track_payload = item.get("track") or {}
        track_id = track_payload.get("id")
        if track_id is None:
            continue
        track = store.get_track(int(track_id))
        if track is None:
            continue
        if store.load_embedding(track.id, model) is None:
            continue
        tracks.append(track)
    return tracks


def build_starred_track_ids(
    store: Store,
    client: NavidromeClient,
    *,
    user: str,
) -> dict[str, Any]:
    return build_starred_track_ids_from_songs(
        store,
        client.get_starred_songs(),
        user=user,
    )


def build_starred_track_ids_from_songs(
    store: Store,
    songs: list[NavidromeSong],
    *,
    user: str,
) -> dict[str, Any]:
    track_ids: list[int] = []
    item_ids: list[str] = []
    not_synced_item_ids: list[str] = []

    for song in songs:
        if not song.id:
            continue
        item_ids.append(song.id)
        track = store.get_track_by_external_id(NAVIDROME_PROVIDER, song.id)
        if track is None:
            not_synced_item_ids.append(song.id)
            continue
        track_ids.append(track.id)

    return {
        "user": user,
        "count": len(item_ids),
        "mapped_count": len(track_ids),
        "track_ids": track_ids,
        "item_ids": item_ids,
        "not_synced_item_ids": not_synced_item_ids,
    }


def _mapped_track_dict(track: Track, *, has_embedding: bool) -> dict[str, Any]:
    return {
        "id": track.id,
        "path": track.path,
        "artist": track.artist,
        "title": track.title,
        "album": track.album,
        "genre": track.genre,
        "year": track.year,
        "duration": track.duration,
        "has_embedding": has_embedding,
    }


def _navidrome_song_dict(song: NavidromeSong) -> dict[str, Any]:
    return {
        "id": None,
        "path": f"navidrome://{song.id}",
        "artist": song.artist,
        "title": song.title,
        "album": song.album,
        "genre": song.genre,
        "year": song.year,
        "duration": float(song.duration) if song.duration is not None else None,
        "has_embedding": False,
    }
