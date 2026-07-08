"""Search and dashboard shelf serializers.

Extracted from app/main.py.
"""
from __future__ import annotations

from app.models import Artist, ReleaseSummaryRow, Track
from app.serializers.entities import (
    _json_object,
    image_ref,
    release_summary_dict,
    track_summary_dict,
)
from app.store import Store


# ---------------------------------------------------------------------------
# Search groups / top result
# ---------------------------------------------------------------------------

def search_group(
    group_type: str,
    title: str,
    items: list[dict[str, object]],
    total: int,
    limit: int,
    offset: int,
) -> dict[str, object]:
    next_offset = offset + limit if offset + limit < total else None
    return {
        "type": group_type,
        "title": title,
        "items": items,
        "total": total,
        "next_offset": next_offset,
    }


def _field_search_score(query: str, value: object) -> int:
    if not isinstance(value, str) or not query:
        return 0
    query_text = query.casefold()
    value_text = value.casefold()
    if value_text == query_text:
        return 100
    if value_text.startswith(query_text):
        return 80
    if query_text in value_text:
        return 50
    return 0


def _entity_search_score(query: str, entity_type: str, entity: dict[str, object]) -> int:
    score = _field_search_score(query, entity.get("name") or entity.get("title"))
    if entity_type in {"release", "track"}:
        for artist in entity.get("artists") or []:
            if isinstance(artist, dict):
                score = max(score, _field_search_score(query, artist.get("name")))
    if entity_type == "track":
        release = entity.get("release")
        if isinstance(release, dict):
            score = max(score, _field_search_score(query, release.get("title")))
    tie_breaker = {"artist": 3, "release": 2, "track": 1}.get(entity_type, 0)
    return score * 10 + tie_breaker if score else tie_breaker


def search_top_result(
    query: str,
    artists: list[dict[str, object]],
    releases: list[dict[str, object]],
    tracks: list[dict[str, object]],
) -> dict[str, object] | None:
    candidates: list[tuple[int, str, dict[str, object]]] = []
    for entity_type, items in (("artist", artists), ("release", releases), ("track", tracks)):
        for item in items:
            candidates.append((_entity_search_score(query, entity_type, item), entity_type, item))
    if not candidates:
        return None
    _score, entity_type, entity = max(candidates, key=lambda item: item[0])
    return {"entity_type": entity_type, "entity": entity}


# ---------------------------------------------------------------------------
# Dashboard shelf helpers
# ---------------------------------------------------------------------------

def _compact_artist_names(artists: list[dict[str, object]]) -> str:
    names = [str(artist.get("name") or "") for artist in artists if artist.get("name")]
    return ", ".join(names) if names else "Unknown artist"


def _artist_links(artists: list[dict[str, object]]) -> list[dict[str, str]]:
    return [
        {"label": str(a.get("name") or ""), "href": f"/artists/{a['id']}"}
        for a in artists
        if a.get("id") and a.get("name")
    ]


def dashboard_shelf_item(
    entity_type: str,
    entity_id: int,
    title: str,
    subtitle: str,
    target: str,
    *,
    artwork_url: str | None = None,
    play_source_type: str | None = None,
    play_source_id: int | None = None,
    reason: str | None = None,
    badges: list[str] | None = None,
    subtitle_links: list[dict[str, str]] | None = None,
    debug: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": f"{entity_type}:{entity_id}",
        "entity_type": entity_type,
        "entity_id": entity_id,
        "title": title,
        "subtitle": subtitle,
        "subtitle_links": subtitle_links or [],
        "artwork": image_ref(artwork_url, "local" if artwork_url else "none"),
        "action": {"type": "open", "target": target},
        "play_action": {
            "type": "play",
            "source_type": play_source_type or entity_type,
            "source_id": play_source_id or entity_id,
        },
        "badges": badges or [],
        "reason": reason,
        "debug": debug,
    }


def _release_shelf_item(row: ReleaseSummaryRow, reason: str | None = None) -> dict[str, object]:
    release = release_summary_dict(row)
    artists = list(release.get("artists") or [])
    artwork = release.get("artwork") if isinstance(release.get("artwork"), dict) else {}
    return dashboard_shelf_item(
        "release",
        int(release["id"]),
        str(release["title"]),
        _compact_artist_names(artists),
        f"/releases/{release['id']}",
        artwork_url=str(artwork.get("url") or "") or None,
        play_source_type="release",
        play_source_id=int(release["id"]),
        reason=reason,
        badges=[str(release.get("release_type_label") or "Release")],
        subtitle_links=_artist_links(artists),
    )


def _track_shelf_item(
    store: Store,
    track: Track,
    artists: list[Artist],
    reason: str | None = None,
) -> dict[str, object]:
    summary = track_summary_dict(store, track, artists)
    release = summary.get("release") if isinstance(summary.get("release"), dict) else None
    target = f"/releases/{release['id']}" if release and release.get("id") else f"?view=recommendations&seed={track.id}"
    artist_objs = list(summary.get("artists") or [])
    return dashboard_shelf_item(
        "track",
        track.id,
        str(summary["title"]),
        _compact_artist_names(artist_objs),
        target,
        artwork_url=f"/api/v1/tracks/{track.id}/cover?size=512",
        play_source_type="track",
        play_source_id=track.id,
        reason=reason,
        badges=[str(release["title"])] if release and release.get("title") else [],
        subtitle_links=_artist_links(artist_objs),
    )


def _discover_track_shelf_item(
    store: Store,
    track: Track,
    artists: list[Artist],
    reason: str | None = None,
) -> dict[str, object]:
    summary = track_summary_dict(store, track, artists)
    release = summary.get("release") if isinstance(summary.get("release"), dict) else None
    target = f"/releases/{release['id']}" if release and release.get("id") else f"?view=recommendations&seed={track.id}"
    artist_objs = list(summary.get("artists") or [])
    item = dashboard_shelf_item(
        "track",
        track.id,
        str(summary["title"]),
        _compact_artist_names(artist_objs),
        target,
        artwork_url=f"/api/v1/tracks/{track.id}/cover?size=512",
        play_source_type="track",
        play_source_id=track.id,
        reason=reason,
        badges=[str(release["title"])] if release and release.get("title") else [],
        subtitle_links=_artist_links(artist_objs),
    )
    # Automatically trigger instant-mix for discover-random tracks on play click
    item["play_action"] = {
        "type": "post",
        "endpoint": f"/api/v1/tracks/{track.id}/instant-mix",
    }
    return item
