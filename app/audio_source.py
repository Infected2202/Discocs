from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import unquote

from app.config import Settings
from app.navidrome import NavidromeClient
from app.store import Store, Track


NAVIDROME_PROVIDER = "navidrome"
NAVIDROME_URI_PREFIX = "navidrome://"


def is_navidrome_track(track: Track) -> bool:
    return str(track.path).startswith(NAVIDROME_URI_PREFIX)


def navidrome_item_id_from_path(path: str) -> str | None:
    if not path.startswith(NAVIDROME_URI_PREFIX):
        return None
    item_id = unquote(path[len(NAVIDROME_URI_PREFIX) :])
    return item_id or None


@contextmanager
def track_audio_path(
    store: Store,
    settings: Settings,
    track: Track,
) -> Iterator[Path]:
    if not is_navidrome_track(track):
        yield Path(track.path)
        return

    item_id = store.external_id_for_track(NAVIDROME_PROVIDER, track.id)
    if item_id is None:
        item_id = navidrome_item_id_from_path(track.path)
    if item_id is None:
        raise ValueError(f"No Navidrome external ID for track {track.id}")

    downloaded = NavidromeClient(settings.navidrome).download_track(
        item_id,
        settings.navidrome.temp_dir,
        suffix=Path(track.path).suffix,
    )
    try:
        yield downloaded.path
    finally:
        try:
            downloaded.path.unlink(missing_ok=True)
        except OSError:
            pass
