"""Entity serializers: artist, release, track dict builders.

Extracted from app/main.py.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.models import Artist, ArtistSummaryRow, Release, ReleaseSummaryRow, ReleaseTrackRow, Track
from app.navidrome import NavidromeClient, artist_info_bio, artist_info_image_url
from app.store import Store

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _json_object(value: str | None) -> dict[str, object]:
    import json
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except Exception:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def image_ref(url: str | None, source: str = "none") -> dict[str, object]:
    return {"url": url, "source": source if url else "none", "placeholder": url is None}


def entity_action(action_type: str, enabled: bool = True, endpoint: str | None = None) -> dict[str, object]:
    return {"type": action_type, "enabled": enabled, "endpoint": endpoint}


def release_type_label(release_type: str) -> str:
    return {
        "album": "Album",
        "ep": "EP",
        "single": "Single",
        "compilation": "Compilation",
        "soundtrack": "Soundtrack",
        "mix": "Mix",
    }.get(release_type, "Release")


# ---------------------------------------------------------------------------
# Artist
# ---------------------------------------------------------------------------

def artist_link_dict(artist: Artist) -> dict[str, object]:
    return {"id": artist.id, "name": artist.name}


def artist_summary_dict(row: ArtistSummaryRow | Artist) -> dict[str, object]:
    artist = row.artist if isinstance(row, ArtistSummaryRow) else row
    track_count = row.track_count if isinstance(row, ArtistSummaryRow) else 0
    release_count = row.release_count if isinstance(row, ArtistSummaryRow) else 0
    return {
        "id": artist.id,
        "name": artist.name,
        "image": image_ref(artist.image_url, "external" if artist.image_url else "none"),
        "library_stats": {
            "tracks": track_count,
            "releases": release_count,
            "liked_tracks": 0,
            "plays": 0,
        },
    }


def artist_summary_with_external_image(
    store: Store,
    settings: object,
    row: ArtistSummaryRow | Artist,
) -> dict[str, object]:
    """Like artist_summary_dict but falls back to Navidrome for missing images."""
    artist = row.artist if isinstance(row, ArtistSummaryRow) else row
    if not artist.image_url:
        external_id = store.external_id_for_entity("navidrome", "artist", artist.id)
        if external_id:
            try:
                info = NavidromeClient(settings.navidrome).get_artist_info2(external_id, count=0)
                image_url = artist_info_image_url(info)
                bio = artist_info_bio(info)
                store.update_artist_external_info(artist.id, image_url=image_url, bio=bio)
                if image_url or bio:
                    refreshed = store.get_artist(artist.id)
                    if refreshed is not None:
                        row = refreshed
            except Exception as exc:
                logger.warning(
                    "Navidrome artist info lookup failed artist_id=%s external_id=%s: %s",
                    artist.id, external_id, exc,
                )
    return artist_summary_dict(row)


# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------

def release_summary_dict(row: ReleaseSummaryRow) -> dict[str, object]:
    release = row.release
    cover_url = f"/api/v1/releases/{release.id}/cover" if release.cover_art_id else None
    return {
        "id": release.id,
        "title": release.title,
        "release_type": release.release_type,
        "release_type_label": release_type_label(release.release_type),
        "artists": [artist_link_dict(artist) for artist in row.artists],
        "release_date": release.release_date,
        "release_year": release.release_year,
        "track_count": release.track_count,
        "duration": release.duration,
        "artwork": image_ref(cover_url, "navidrome" if cover_url else "none"),
    }


# ---------------------------------------------------------------------------
# Track
# ---------------------------------------------------------------------------

def _track_release_summary(store: Store, track_id: int) -> dict[str, object] | None:
    with store.connect() as conn:
        row = conn.execute(
            """
            SELECT r.id, r.title
            FROM release_tracks rt
            JOIN releases r ON r.id = rt.release_id
            WHERE rt.track_id = ?
            ORDER BY rt.position
            LIMIT 1
            """,
            (track_id,),
        ).fetchone()
    if row is None:
        return None
    return {"id": int(row["id"]), "title": str(row["title"])}


def track_summary_dict(store: Store, track: Track, artists: list[Artist] | None = None) -> dict[str, object]:
    release = _track_release_summary(store, track.id)
    navidrome_item_id = store.external_id_for_track("navidrome", track.id)
    return {
        "id": track.id,
        "title": track.title or Path(track.path).stem,
        "artist": track.artist,
        "album": track.album,
        "artists": [artist_link_dict(artist) for artist in (artists or [])],
        "duration": track.duration,
        "release": release,
        "artwork": image_ref(f"/tracks/{track.id}/cover", "local"),
        "navidrome_item_id": navidrome_item_id,
        "explicit": False,
        "liked": False,
        "actions": [],
    }


def release_track_dict(store: Store, item: ReleaseTrackRow) -> dict[str, object]:
    data = track_summary_dict(store, item.track, item.artists)
    data.update({
        "disc_number": item.disc_number,
        "track_number": item.track_number,
        "position": item.position,
    })
    return data
