"""Artists API routes.

Extracted from app/main.py — Stage 6b.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, Response

from app.api.deps import api_error, context
from app.models import ArtistSummaryRow
from app.navidrome import download_image
from app.schemas.responses import (
    ArtistAvailabilityStubResponse,
    ArtistDiscographyResponse,
    ArtistResponse,
    ImageInfoResponse,
)
from app.serializers.entities import (
    artist_link_dict,
    artist_summary_with_external_image,
    ensure_artist_external_info,
    entity_action,
    image_ref,
    release_summary_dict,
    release_track_dict,
    track_summary_dict,
)
from app.services.cover import (
    cached_cover_error,
    cached_cover_response,
    cover_response,
    remember_cover,
    remember_cover_error,
)
from app.state import COVER_TIMEOUT_SECONDS
from app.services.artist_similarity import find_similar_artists

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

_ARTIST_NOT_FOUND = "Artist not found"
_ARTIST_IMAGE_NOT_AVAILABLE = "Artist image not available"


@router.get("/artists/{artist_id}", response_model=ArtistResponse)
def api_v1_artist(artist_id: int) -> dict[str, object] | JSONResponse:
    store, settings = context()
    artist = store.get_artist(artist_id)
    if artist is None:
        return api_error(404, "not_found", _ARTIST_NOT_FOUND)
    top = store.top_tracks_for_artist(artist_id, limit=100)
    artists_by_track = store.artists_for_tracks([track.id for track, _ in top])
    top_tracks = []
    for track, play_count in top:
        item = track_summary_dict(store, track, artists_by_track.get(track.id, []))
        item["play_count"] = play_count
        top_tracks.append(item)
    return {
        "artist": {**artist_summary_with_external_image(store, settings, artist), "sort_name": artist.artist.sort_name},
        "actions": [entity_action("mix", True, None)],
        "links": {
            "image": f"/api/v1/artists/{artist_id}/image",
            "discography": f"/api/v1/artists/{artist_id}/discography",
            "top_tracks": f"/api/v1/artists/{artist_id}/top-tracks",
            "similar": f"/api/v1/artists/{artist_id}/similar",
        },
        "top_tracks": top_tracks,
    }


@router.get("/artists/{artist_id}/discography", response_model=ArtistDiscographyResponse)
def api_v1_artist_discography(
    artist_id: int,
    sort: Annotated[str, Query(pattern="^(release_date_desc|release_date_asc|title)$")] = "release_date_desc",
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    include_tracks: bool = False,
) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    artist = store.get_artist(artist_id)
    if artist is None:
        return api_error(404, "not_found", _ARTIST_NOT_FOUND)
    # Порядок ключей = порядок шелфов на странице артиста (фронт рендерит
    # группы как пришли). "Featured In" (релизы, где артист лишь приглашённый)
    # держим в самом низу — это вторичная дискография.
    titles = {
        "albums": "Albums",
        "eps": "EPs",
        "singles": "Singles",
        "compilations": "Compilations",
        "releases": "Releases",
        "featured_in": "Featured In",
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


@router.get("/artists/{artist_id}/image", response_model=ImageInfoResponse)
def api_v1_artist_image(artist_id: int) -> dict[str, object] | JSONResponse:
    store, settings = context()
    artist = store.get_artist(artist_id)
    if artist is None:
        return api_error(404, "not_found", _ARTIST_NOT_FOUND)
    summary = artist_summary_with_external_image(store, settings, artist)
    image = summary["image"] if isinstance(summary.get("image"), dict) else image_ref(None)
    if not image.get("url"):
        return api_error(404, "not_found", _ARTIST_IMAGE_NOT_AVAILABLE)
    return {"image": image}


@router.get("/artists/{artist_id}/cover", response_model=None)
def api_v1_artist_cover(artist_id: int) -> Response | JSONResponse:
    """Serve the artist image bytes, proxying the stored Navidrome URL.

    The URL in ``artists.image_url`` points at the LAN-internal Navidrome
    address — unreachable from outside the LAN and blocked as mixed content
    on an HTTPS page, so a redirect would not work for remote clients.
    """
    store, settings = context()
    artist = store.get_artist(artist_id)
    if artist is None:
        return api_error(404, "not_found", _ARTIST_NOT_FOUND)
    row = ensure_artist_external_info(store, settings, artist)
    url = (row.artist if isinstance(row, ArtistSummaryRow) else row).image_url
    if not url:
        return api_error(404, "not_found", _ARTIST_IMAGE_NOT_AVAILABLE)

    cache_key = ("artist", artist_id)
    cached = cached_cover_response(cache_key)
    if cached is not None:
        return cover_response(*cached)
    if cached_cover_error(cache_key) is not None:
        return api_error(404, "not_found", _ARTIST_IMAGE_NOT_AVAILABLE)
    try:
        image = download_image(url, timeout=COVER_TIMEOUT_SECONDS)
    except Exception as exc:
        logger.warning("Artist image download failed artist_id=%s url=%s: %s", artist_id, url, exc)
        remember_cover_error(cache_key, str(exc))
        return api_error(404, "not_found", _ARTIST_IMAGE_NOT_AVAILABLE)
    remember_cover(cache_key, image.payload, image.content_type)
    return cover_response(image.payload, image.content_type)


@router.get("/artists/{artist_id}/top-tracks", response_model=ArtistAvailabilityStubResponse)
def api_v1_artist_top_tracks(artist_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    artist = store.get_artist(artist_id)
    if artist is None:
        return api_error(404, "not_found", _ARTIST_NOT_FOUND)
    top = store.top_tracks_for_artist(artist_id, limit=100)
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


@router.get("/artists/{artist_id}/similar", response_model=ArtistAvailabilityStubResponse)
def api_v1_artist_similar(
    artist_id: int,
    limit: Annotated[int, Query(ge=1, le=50)] = 16,
    include_debug: bool = False,
) -> dict[str, object] | JSONResponse:
    store, settings = context()
    artist = store.get_artist(artist_id)
    if artist is None:
        return api_error(404, "not_found", _ARTIST_NOT_FOUND)
    model_name = settings.default_model
    if store.load_artist_embedding(artist_id, model_name) is None:
        return {
            "artist": artist_link_dict(artist.artist),
            "items": [],
            "available": False,
            "basis": "no_aggregate",
        }

    items: list[dict[str, object]] = []
    for result in find_similar_artists(store, model_name, artist_id, limit=limit):
        candidate = store.get_artist(result.artist_id)
        if candidate is None:
            continue
        item = artist_summary_with_external_image(store, settings, candidate)
        if include_debug:
            item["score"] = result.score
            item["centroid_similarity"] = result.centroid_similarity
            item["catalog_similarity"] = result.catalog_similarity
        items.append(item)
    return {
        "artist": artist_link_dict(artist.artist),
        "items": items,
        "available": True,
        "basis": "artist_similarity",
    }
