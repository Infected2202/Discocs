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


def generate_mix_cover(
    store: Store,
    settings: Settings,
    mix_id: str,
    track_ids: list[int],
) -> Path | None:
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow is not installed; generated mix cover skipped mix_id=%s", mix_id)
        return None

    covers = _load_track_cover_images(store, settings, track_ids[:4])
    if not covers:
        logger.info("No source covers available for generated mix cover mix_id=%s", mix_id)
        return None

    canvas = Image.new("RGB", (MIX_COVER_SIZE, MIX_COVER_SIZE), "#111518")
    positions = [
        (0, 0),
        (MIX_COVER_TILE_SIZE, 0),
        (0, MIX_COVER_TILE_SIZE),
        (MIX_COVER_TILE_SIZE, MIX_COVER_TILE_SIZE),
    ]
    for index, image in enumerate(covers[:4]):
        tile = _square_tile(image, MIX_COVER_TILE_SIZE)
        canvas.paste(tile, positions[index])

    target_dir = settings.data_dir / "mix_covers"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{_safe_mix_id(mix_id)}.jpg"
    tmp = target.with_suffix(".tmp.jpg")
    canvas.save(tmp, format="JPEG", quality=88, optimize=True)
    tmp.replace(target)
    return target


def _load_track_cover_images(store: Store, settings: Settings, track_ids: list[int]):
    from PIL import Image

    if not settings.navidrome.url or not settings.navidrome.user or not settings.navidrome.password:
        logger.info("Navidrome settings are incomplete; generated mix cover skipped")
        return []
    try:
        client = NavidromeClient(settings.navidrome)
    except ValueError:
        logger.info("Navidrome settings are invalid; generated mix cover skipped", exc_info=True)
        return []
    images = []
    seen_cover_ids: set[str] = set()
    for track_id in track_ids:
        cover_art_id = _track_cover_art_id(store, track_id)
        if not cover_art_id or cover_art_id in seen_cover_ids:
            continue
        seen_cover_ids.add(cover_art_id)
        try:
            cover = client.get_cover_art(cover_art_id, size=MIX_COVER_TILE_SIZE)
            image = Image.open(BytesIO(cover.payload)).convert("RGB")
        except Exception:
            logger.warning(
                "Generated mix source cover unavailable track_id=%s cover_art_id=%s",
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
