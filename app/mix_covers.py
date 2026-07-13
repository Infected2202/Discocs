"""Collage cover generation for generated mixes and user playlists.

A cover is a 2x2 grid of track artwork tiles on a dark background; missing
tiles stay dark grey. Mixes deduplicate identical artwork so one album does
not fill the whole grid, playlists show the first four tracks verbatim.
"""
from __future__ import annotations

from io import BytesIO
import json
import logging
from pathlib import Path
import re

from app.config import Settings
from app.navidrome import NavidromeClient
from app.store import Store


logger = logging.getLogger(__name__)
MIX_COVER_SIZE = 600
MIX_COVER_TILE_SIZE = MIX_COVER_SIZE // 2
COLLAGE_BACKGROUND = "#111518"


def generate_mix_cover(
    store: Store,
    settings: Settings,
    mix_id: str,
    track_ids: list[int],
) -> Path | None:
    target = settings.data_dir / "mix_covers" / f"{_safe_mix_id(mix_id)}.jpg"
    return _generate_collage_cover(
        store,
        settings,
        track_ids[:4],
        target=target,
        dedup_covers=True,
        log_label=f"mix_id={mix_id}",
    )


def generate_playlist_cover(
    store: Store,
    settings: Settings,
    playlist_id: int,
    track_ids: list[int],
) -> Path | None:
    target = settings.data_dir / "playlist_covers" / f"{int(playlist_id)}.jpg"
    return _generate_collage_cover(
        store,
        settings,
        track_ids[:4],
        target=target,
        dedup_covers=False,
        log_label=f"playlist_id={playlist_id}",
    )


def refresh_playlist_cover(store: Store, settings: Settings, playlist_id: int) -> str | None:
    """Regenerate the playlist collage after a mutation; returns the stored cover path.

    An empty playlist loses its cover. If generation fails for a non-empty
    playlist (Navidrome down, Pillow missing) the previous cover is kept —
    a stale collage beats no artwork.
    """
    playlist = store.get_owned_playlist(playlist_id)
    if playlist is None:
        return None
    track_ids = store.playlist_track_ids(playlist_id)
    if not track_ids:
        if playlist.cover_path:
            Path(playlist.cover_path).unlink(missing_ok=True)
        store.set_playlist_cover_path(playlist_id, None)
        return None
    cover_path = generate_playlist_cover(store, settings, playlist_id, track_ids)
    if cover_path is None:
        return playlist.cover_path
    updated = store.set_playlist_cover_path(playlist_id, str(cover_path))
    return updated.cover_path if updated else str(cover_path)


def _generate_collage_cover(
    store: Store,
    settings: Settings,
    track_ids: list[int],
    *,
    target: Path,
    dedup_covers: bool,
    log_label: str,
) -> Path | None:
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow is not installed; collage cover skipped %s", log_label)
        return None

    covers = _load_track_cover_images(store, settings, track_ids, dedup_covers=dedup_covers)
    if not covers:
        logger.info("No source covers available for collage cover %s", log_label)
        return None

    canvas = Image.new("RGB", (MIX_COVER_SIZE, MIX_COVER_SIZE), COLLAGE_BACKGROUND)
    positions = [
        (0, 0),
        (MIX_COVER_TILE_SIZE, 0),
        (0, MIX_COVER_TILE_SIZE),
        (MIX_COVER_TILE_SIZE, MIX_COVER_TILE_SIZE),
    ]
    for index, image in enumerate(covers[:4]):
        tile = _square_tile(image, MIX_COVER_TILE_SIZE)
        canvas.paste(tile, positions[index])

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp.jpg")
    canvas.save(tmp, format="JPEG", quality=88, optimize=True)
    tmp.replace(target)
    return target


def _load_track_cover_images(
    store: Store,
    settings: Settings,
    track_ids: list[int],
    *,
    dedup_covers: bool = True,
):
    from PIL import Image

    if not settings.navidrome.url or not settings.navidrome.user or not settings.navidrome.password:
        logger.info("Navidrome settings are incomplete; collage cover skipped")
        return []
    try:
        client = NavidromeClient(settings.navidrome)
    except ValueError:
        logger.info("Navidrome settings are invalid; collage cover skipped", exc_info=True)
        return []
    images = []
    seen_cover_ids: set[str] = set()
    for track_id in track_ids:
        cover_art_id = _track_cover_art_id(store, track_id)
        if not cover_art_id:
            continue
        if dedup_covers:
            if cover_art_id in seen_cover_ids:
                continue
            seen_cover_ids.add(cover_art_id)
        try:
            cover = client.get_cover_art(cover_art_id, size=MIX_COVER_TILE_SIZE)
            image = Image.open(BytesIO(cover.payload)).convert("RGB")
        except Exception:
            logger.warning(
                "Collage source cover unavailable track_id=%s cover_art_id=%s",
                track_id,
                cover_art_id,
                exc_info=True,
            )
            continue
        images.append(image)
    return images


def _track_cover_art_id(store: Store, track_id: int) -> str | None:
    external_id = store.external_id_for_track("navidrome", track_id)
    if external_id is None:
        return None
    mapping = store.get_external_track("navidrome", external_id)
    if mapping is None or not mapping.raw_json:
        return None
    try:
        parsed = json.loads(mapping.raw_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    cover_art_id = parsed.get("coverArt") or parsed.get("coverArtId") or parsed.get("cover_art_id")
    return str(cover_art_id) if cover_art_id else None


def _square_tile(image, size: int):
    width, height = image.size
    side = min(width, height)
    left = max((width - side) // 2, 0)
    top = max((height - side) // 2, 0)
    cropped = image.crop((left, top, left + side, top + side))
    return cropped.resize((size, size))


def _safe_mix_id(mix_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", mix_id).strip("-") or "mix"
