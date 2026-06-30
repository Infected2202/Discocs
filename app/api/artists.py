"""Artists API routes.

Extracted from app/main.py — Stage 6b.
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, RedirectResponse

from app.api.deps import api_error, context
from app.schemas.responses import (
    ArtistAvailabilityStubResponse,
    ArtistDiscographyResponse,
    ArtistResponse,
    ImageInfoResponse,
)
from app.serializers.entities import (
    artist_link_dict,
    artist_summary_with_external_image,
    entity_action,
    image_ref,
    release_summary_dict,
    release_track_dict,
    track_summary_dict,
)

router = APIRouter()


@router.get("/api/v1/artists/{artist_id}", response_model=ArtistResponse)
def api_v1_artist(artist_id: int) -> dict[str, object] | JSONResponse:
    store, settings = context()
    artist = store.get_artist(artist_id)
    if artist is None:
        return api_error(404, "not_found", "Artist not found")
    return {
        "artist": {**artist_summary_with_external_image(store, settings, artist), "sort_name": artist.artist.sort_name},
        "actions": [entity_action("mix", True, None)],
        "links": {
            "image": f"/api/v1/artists/{artist_id}/image",
            "discography": f"/api/v1/artists/{artist_id}/discography",
            "top_tracks": f"/api/v1/artists/{artist_id}/top-tracks",
            "similar": f"/api/v1/artists/{artist_id}/similar",
        },
    }


@router.get("/api/v1/artists/{artist_id}/discography", response_model=ArtistDiscographyResponse)
def api_v1_artist_discography(
    artist_id: int,
    sort: str = Query(default="release_date_desc", pattern="^(release_date_desc|release_date_asc|title)$"),
    limit: int | None = Query(default=None, ge=1, le=100),
    include_tracks: bool = False,
) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    artist = store.get_artist(artist_id)
    if artist is None:
        return api_error(404, "not_found", "Artist not found")
    titles = {
        "albums": "Albums",
        "eps": "EPs",
        "singles": "Singles",
        "compilations": "Compilations",
        "featured_in": "Featured In",
        "releases": "Releases",
    }
    discography = store.artist_discography(artist_id)
    groups = []
    for key, title in titles.items():
        items = discography[key]
        if sort == "title":
            items = sorted(items, key=lambda item: item.release.title.casefold())
        elif sort == "release_date_asc":
            items = sorted(
                items,
                key=lambda item: (
                    item.release.release_year is None,
                    item.release.release_year or 0,
                    item.release.title.casefold(),
                ),
            )
        if limit is not None:
            items = items[:limit]
        release_items = []
        for item in items:
            release_item = release_summary_dict(item)
            if include_tracks:
                release_item["tracks"] = [
                    release_track_dict(store, track)
                    for track in store.list_release_tracks(item.release.id)
                ]
            release_items.append(release_item)
        groups.append({"key": key, "title": title, "items": release_items})
    return {"artist": artist_link_dict(artist.artist), "groups": groups}


@router.get("/api/v1/artists/{artist_id}/image", response_model=ImageInfoResponse)
def api_v1_artist_image(artist_id: int) -> dict[str, object] | JSONResponse:
    store, settings = context()
    artist = store.get_artist(artist_id)
    if artist is None:
        return api_error(404, "not_found", "Artist not found")
    summary = artist_summary_with_external_image(store, settings, artist)
    image = summary["image"] if isinstance(summary.get("image"), dict) else image_ref(None)
    if not image.get("url"):
        return api_error(404, "not_found", "Artist image not available")
    return {"image": image}


@router.get("/api/v1/artists/{artist_id}/cover", response_model=None)
def api_v1_artist_cover(artist_id: int) -> JSONResponse | RedirectResponse:
    """Redirect to the artist's image URL, fetching from Navidrome if not yet cached."""
    store, settings = context()
    artist = store.get_artist(artist_id)
    if artist is None:
        return api_error(404, "not_found", "Artist not found")
    summary = artist_summary_with_external_image(store, settings, artist)
    image = summary["image"] if isinstance(summary.get("image"), dict) else {}
    url = image.get("url")
    if not url:
        return api_error(404, "not_found", "Artist image not available")
    return RedirectResponse(url=str(url), status_code=302)


@router.get("/api/v1/artists/{artist_id}/top-tracks", response_model=ArtistAvailabilityStubResponse)
def api_v1_artist_top_tracks(artist_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    artist = store.get_artist(artist_id)
    if artist is None:
        return api_error(404, "not_found", "Artist not found")
    top = store.top_tracks_for_artist(artist_id, limit=20)
    artists_by_track = store.artists_for_tracks([track.id for track, _ in top])
    items = []
    for track, play_count in top:
        item = track_summary_dict(store, track, artists_by_track.get(track.id, []))
        item["play_count"] = play_count
        items.append(item)
    return {
        "artist": artist_link_dict(artist.artist),
        "items": items,
        "basis": "local_playback",
        "available": len(items) > 0,
    }


@router.get("/api/v1/artists/{artist_id}/similar", response_model=ArtistAvailabilityStubResponse)
def api_v1_artist_similar(artist_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    artist = store.get_artist(artist_id)
    if artist is None:
        return api_error(404, "not_found", "Artist not found")
    return {"artist": artist_link_dict(artist.artist), "items": [], "available": False, "basis": "not_available"}
