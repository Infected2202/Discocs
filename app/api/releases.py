"""Releases API routes.

Extracted from app/main.py — Stage 6b.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, Response

from app.api.deps import api_error, context
from app.navidrome import NavidromeClient
from app.schemas.responses import (
    RelatedDiscographyResponse,
    ReleaseAvailabilityStubResponse,
    ReleaseResponse,
    ReleaseTracksResponse,
)
from app.serializers.entities import (
    artist_link_dict,
    entity_action,
    release_summary_dict,
    release_track_dict,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/v1/releases/{release_id}", response_model=ReleaseResponse)
def api_v1_release(release_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    release = store.get_release(release_id)
    if release is None:
        return api_error(404, "not_found", "Release not found")
    return {
        "release": release_summary_dict(release),
        "actions": [entity_action("play", True, None), entity_action("shuffle", True, None)],
        "links": {
            "tracks": f"/api/v1/releases/{release_id}/tracks",
            "discography": f"/api/v1/releases/{release_id}/related-discography",
            "recommendations": f"/api/v1/releases/{release_id}/recommendations",
        },
    }


@router.get("/api/v1/releases/{release_id}/tracks", response_model=ReleaseTracksResponse)
def api_v1_release_tracks(release_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    release = store.get_release(release_id)
    if release is None:
        return api_error(404, "not_found", "Release not found")
    return {
        "release": {"id": release.release.id, "title": release.release.title},
        "items": [release_track_dict(store, item) for item in store.list_release_tracks(release_id)],
    }


@router.get("/api/v1/releases/{release_id}/related-discography", response_model=RelatedDiscographyResponse)
def api_v1_release_related_discography(release_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    release = store.get_release(release_id)
    if release is None:
        return api_error(404, "not_found", "Release not found")
    items = store.related_discography_for_release(release_id)
    return {
        "release": {"id": release.release.id, "title": release.release.title},
        "context_artists": [
            artist_link_dict(artist)
            for artist in store.participating_artists_for_release(release_id)
        ],
        "items": [release_summary_dict(item) for item in items],
    }


@router.get("/api/v1/releases/{release_id}/recommendations", response_model=ReleaseAvailabilityStubResponse)
def api_v1_release_recommendations(release_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    release = store.get_release(release_id)
    if release is None:
        return api_error(404, "not_found", "Release not found")
    return {
        "release": {"id": release.release.id, "title": release.release.title},
        "available": False,
        "basis": "not_available",
        "items": [],
    }


@router.get("/api/v1/releases/{release_id}/cover", response_model=None)
def api_v1_release_cover(
    release_id: int,
    size: int = Query(default=300, ge=32, le=1000),
) -> Response | JSONResponse:
    store, settings = context()
    release = store.get_release(release_id)
    if release is None:
        return api_error(404, "not_found", "Release not found")
    if not release.release.cover_art_id:
        return api_error(404, "not_found", "Release has no cover art")
    try:
        cover = NavidromeClient(settings.navidrome).get_cover_art(release.release.cover_art_id, size=size)
    except Exception as exc:
        logger.warning("Release cover lookup failed release_id=%s: %s", release_id, exc)
        return api_error(404, "not_found", "Release cover not available")
    return Response(content=cover.payload, media_type=cover.content_type)
