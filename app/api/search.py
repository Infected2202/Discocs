"""Search API routes.

Extracted from app/main.py — Stage 6b.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import context
from app.schemas.responses import SearchResponse
from app.serializers.entities import (
    artist_summary_with_external_image,
    release_summary_dict,
    track_summary_dict,
)
from app.serializers.search import (
    _entity_search_score,
    search_group,
    search_top_result,
)

router = APIRouter()


@router.get("/api/v1/search", response_model=SearchResponse)
def api_v1_search(
    q: str = "",
    type: str = Query(default="all", pattern="^(all|artist|release|track)$"),
    limit: int = Query(default=8, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    include_debug: bool = False,
) -> dict[str, object]:
    store, settings = context()
    query = " ".join(q.strip().split())
    results = store.search_entities(query, entity_type=type, limit=limit, offset=offset)
    artist_rows = results["artists"]["items"]
    release_rows = results["releases"]["items"]
    track_rows = results["tracks"]["items"]
    artists = [artist_summary_with_external_image(store, settings, row) for row in artist_rows]
    releases = [release_summary_dict(row) for row in release_rows]
    artists_by_track = store.artists_for_tracks([track.id for track in track_rows])
    tracks = [
        track_summary_dict(store, track, artists_by_track.get(track.id, []))
        for track in track_rows
    ]
    groups = [
        search_group("artists", "Artists", artists, int(results["artists"]["total"]), limit, offset),
        search_group("tracks", "Tracks", tracks, int(results["tracks"]["total"]), limit, offset),
        search_group("releases", "Releases", releases, int(results["releases"]["total"]), limit, offset),
    ]
    top_result = search_top_result(query, artists, releases, tracks)
    if include_debug:
        for group in groups:
            for item in group["items"]:
                if isinstance(item, dict):
                    item["debug"] = {
                        "search_score": _entity_search_score(
                            query,
                            str(group["type"]).rstrip("s"),
                            item,
                        )
                    }
    return {"query": query, "top_result": top_result, "groups": groups}
