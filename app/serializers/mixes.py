"""Generated mix serializers.

Extracted from app/main.py.
"""
from __future__ import annotations

from urllib.parse import quote

from app.serializers.entities import _json_object, image_ref, track_summary_dict
from app.store import Store


def mix_api_base(owner: str | None) -> str:
    """API prefix of a mix: the viewer's own, or one read on another user's profile."""
    if owner is None:
        return "/api/v1/mixes"
    return f"/api/v1/users/{quote(owner, safe='')}/mixes"


def mix_page_path(mix_id: str, owner: str | None) -> str:
    """UI route of a mix page: ``/mixes/{id}``, or the read-only one under the owner's profile."""
    if owner is None:
        return f"/mixes/{mix_id}"
    return f"/u/{quote(owner, safe='')}/mixes/{mix_id}"


def _generated_mix_artwork(mix, items, owner: str | None = None) -> dict[str, object]:
    if mix.cover_path:
        return image_ref(f"{mix_api_base(owner)}/{mix.id}/cover", "generated_mix")
    if items:
        return image_ref(f"/api/v1/tracks/{items[0].track_id}/cover?size=512", "track")
    return image_ref(None, "none")


def _generated_mix_subtitle(anchor: dict[str, object], item_count: int) -> str:
    subtitle_parts = [
        str(anchor.get("representative_artist") or ""),
        str(anchor.get("representative_album") or ""),
    ]
    subtitle = ", ".join(value for value in subtitle_parts if value)
    if subtitle:
        return subtitle
    return f"{item_count} tracks"


def generated_mix_summary_dict(store: Store, mix, owner: str | None = None) -> dict[str, object]:
    """Summary of a mix. ``owner`` is set when the mix is another user's (profile):
    the links then go through the owner's profile, where the viewer may read it."""
    items = store.list_generated_mix_items(mix.id)
    anchor = _json_object(mix.anchor_json)
    settings = _json_object(mix.settings_json)
    score_summary = _json_object(mix.score_summary_json)
    subtitle = _generated_mix_subtitle(anchor, len(items))
    artwork = _generated_mix_artwork(mix, items, owner)
    return {
        "id": mix.id,
        "title": mix.title,
        "mix_type": mix.mix_type,
        "status": mix.status,
        "subtitle": subtitle,
        "track_count": len(items),
        "artwork": artwork,
        "anchor": anchor,
        "settings": settings,
        "score_summary": score_summary,
        "created_at": mix.created_at,
        "updated_at": mix.updated_at,
        "expires_at": mix.expires_at,
        "saved_playlist_id": mix.saved_playlist_id,
        "action": {"type": "open", "target": mix_page_path(mix.id, owner)},
        "play_action": {"type": "post", "endpoint": f"{mix_api_base(owner)}/{mix.id}/play"},
    }


def generated_mix_shelf_item(
    store: Store,
    mix,
    *,
    owner: str | None = None,
    include_debug: bool = False,
) -> dict[str, object]:
    """A mix as a shelf card (dashboard "Mixes For You", the profile's mixes shelf)."""
    summary = generated_mix_summary_dict(store, mix, owner)
    item: dict[str, object] = {
        "id": f"generated_mix:{mix.id}",
        "entity_type": "generated_mix",
        "entity_id": mix.id,
        "title": summary["title"],
        "subtitle": summary["subtitle"],
        "artwork": summary["artwork"],
        "action": summary["action"],
        "play_action": summary["play_action"],
        "badges": [str(summary["track_count"]) + " tracks", str(mix.status)],
    }
    if include_debug:
        item["debug"] = {
            "anchor": summary["anchor"],
            "score_summary": summary["score_summary"],
            "settings": summary["settings"],
        }
    return item


def generated_mix_detail_dict(store: Store, mix) -> dict[str, object]:
    item_rows = store.list_generated_mix_items(mix.id)
    tracks = store.get_tracks([item.track_id for item in item_rows])
    artists_by_track = store.artists_for_tracks([item.track_id for item in item_rows])
    items: list[dict[str, object]] = []
    for item in item_rows:
        track = tracks.get(item.track_id)
        items.append(
            {
                "mix_id": item.mix_id,
                "position": item.position,
                "track_id": item.track_id,
                "track": track_summary_dict(store, track, artists_by_track.get(item.track_id, [])) if track else None,
                "score": item.score,
                "score_breakdown": _json_object(item.score_breakdown_json),
                "reason": _json_object(item.reason_json),
                "created_at": item.created_at,
            }
        )
    summary = generated_mix_summary_dict(store, mix)
    summary["items"] = items
    summary["actions"] = {
        "save": {"method": "POST", "endpoint": f"/api/v1/mixes/{mix.id}/save"},
        "play": {"method": "POST", "endpoint": f"/api/v1/mixes/{mix.id}/play"},
    }
    return summary
