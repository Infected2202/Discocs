"""Releases API routes.

Extracted from app/main.py — Stage 6b.
"""
from __future__ import annotations

import logging
from typing import Annotated

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
from app.serializers.search import _release_shelf_item
from app.services.release_similarity import find_similar_releases

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

_RELEASE_NOT_FOUND = "Release not found"


@router.get("/releases/{release_id}", response_model=ReleaseResponse)
def api_v1_release(release_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    release = store.get_release(release_id)
    if release is None:
        return api_error(404, "not_found", _RELEASE_NOT_FOUND)
    return {
        "release": release_summary_dict(release),
        "actions": [entity_action("play", True, None), entity_action("shuffle", True, None)],
        "links": {
            "tracks": f"/api/v1/releases/{release_id}/tracks",
            "discography": f"/api/v1/releases/{release_id}/related-discography",
            "recommendations": f"/api/v1/releases/{release_id}/recommendations",
        },
    }


@router.get("/releases/{release_id}/tracks", response_model=ReleaseTracksResponse)
def api_v1_release_tracks(release_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    release = store.get_release(release_id)
    if release is None:
        return api_error(404, "not_found", _RELEASE_NOT_FOUND)
    return {
        "release": {"id": release.release.id, "title": release.release.title},
        "items": [release_track_dict(store, item) for item in store.list_release_tracks(release_id)],
    }


@router.get("/releases/{release_id}/related-discography", response_model=RelatedDiscographyResponse)
def api_v1_release_related_discography(release_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    release = store.get_release(release_id)
    if release is None:
        return api_error(404, "not_found", _RELEASE_NOT_FOUND)
    items = store.related_discography_for_release(release_id)
    return {
        "release": {"id": release.release.id, "title": release.release.title},
        "context_artists": [
            artist_link_dict(artist)
            for artist in store.participating_artists_for_release(release_id)
        ],
        "items": [release_summary_dict(item) for item in items],
    }


@router.get("/releases/{release_id}/recommendations", response_model=None)
def api_v1_release_recommendations(
    release_id: int,
    limit: Annotated[int, Query(ge=1, le=50)] = 12,
    include_debug: bool = False,
) -> dict[str, object] | JSONResponse:
    store, settings = context()
    release = store.get_release(release_id)
    if release is None:
        return api_error(404, "not_found", _RELEASE_NOT_FOUND)

    model_name = settings.default_model
    source_centroid = store.load_release_embedding(release_id, model_name)

    if source_centroid is None:
        return {
            "release": {"id": release.release.id, "title": release.release.title},
            "available": False,
            "basis": "no_aggregate",
            "items": [],
        }

    # Exclude the source release and all other releases by the same artist(s)
    artist_ids = store.artist_ids_for_release(release_id)
    exclude_ids: set[int] = {release_id}
    if artist_ids:
        with store.connect() as conn:
            placeholders = ",".join("?" for _ in artist_ids)
            rows = conn.execute(
                f"SELECT DISTINCT release_id FROM release_artists WHERE artist_id IN ({placeholders})",
                artist_ids,
            ).fetchall()
        exclude_ids.update(int(r[0]) for r in rows)

    similar = find_similar_releases(
        store,
        model_name,
        source_centroid,
        exclude_release_ids=exclude_ids,
        limit=limit,
    )

    items: list[dict[str, object]] = []
    for rel_id, score in similar:
        row = store.get_release(rel_id)
        if row is None:
            continue
        item = _release_shelf_item(row)
        if include_debug:
            item["score"] = score
        items.append(item)

    return {
        "release": {"id": release.release.id, "title": release.release.title},
        "available": True,
        "basis": "release_similarity",
        "items": items,
    }


@router.get("/releases/{release_id}/cover", response_model=None)
def api_v1_release_cover(
    release_id: int,
    size: Annotated[int, Query(ge=32, le=1000)] = 300,
) -> Response | JSONResponse:
    store, settings = context()
    release = store.get_release(release_id)
    if release is None:
        return api_error(404, "not_found", _RELEASE_NOT_FOUND)
    if not release.release.cover_art_id:
        return api_error(404, "not_found", "Release has no cover art")
    try:
        cover = NavidromeClient(settings.navidrome).get_cover_art(release.release.cover_art_id, size=size)
    except Exception as exc:
        logger.warning("Release cover lookup failed release_id=%s: %s", release_id, exc)
        return api_error(404, "not_found", "Release cover not available")
    return Response(content=cover.payload, media_type=cover.content_type)
