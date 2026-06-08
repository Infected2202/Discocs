from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Callable

from app.navidrome import NavidromeClient, NavidromeSong
from app.scanner import ScannedTrack
from app.store import Store


NAVIDROME_PROVIDER = "navidrome"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NavidromeSyncResult:
    seen_count: int
    imported_count: int
    updated_count: int
    stale_count: int
    failed_count: int
    external_id_count: int
    tracks_without_external_id: int

    def summary(self) -> str:
        return (
            f"seen={self.seen_count} imported={self.imported_count} "
            f"updated={self.updated_count} stale={self.stale_count} "
            f"failed={self.failed_count} external_ids={self.external_id_count} "
            f"tracks_without_external_id={self.tracks_without_external_id}"
        )


ProgressCallback = Callable[[int, NavidromeSong], None]


def sync_navidrome_catalog(
    store: Store,
    client: NavidromeClient,
    *,
    page_size: int = 500,
    limit: int | None = None,
    mark_stale: bool = True,
    progress: ProgressCallback | None = None,
) -> NavidromeSyncResult:
    logger.info(
        "Starting Navidrome sync page_size=%s limit=%s mark_stale=%s",
        page_size,
        limit,
        mark_stale,
    )
    existing_external_ids = {
        item.external_id
        for item in store.list_external_tracks(NAVIDROME_PROVIDER)
    }
    seen_external_ids: set[str] = set()
    imported_count = 0
    updated_count = 0
    failed_count = 0

    for song in client.iter_songs(page_size=page_size, limit=limit):
        if progress is not None:
            progress(len(seen_external_ids) + 1, song)
        try:
            if not song.id:
                failed_count += 1
                logger.warning("Skipping Navidrome song without id raw=%s", song.raw)
                continue
            seen_external_ids.add(song.id)
            existing_mapping = store.get_external_track(NAVIDROME_PROVIDER, song.id)
            raw_json = json.dumps(song.raw or {}, sort_keys=True)
            if existing_mapping is not None:
                track_id = existing_mapping.track_id
                changed = False
                mapped_track = store.get_track(track_id)
                if mapped_track is not None and mapped_track.path.startswith("navidrome://"):
                    track_id, changed = store.upsert_track(_song_to_scanned_track(song))
            else:
                track_id, changed = store.upsert_track(_song_to_scanned_track(song))
            raw_changed = existing_mapping is not None and existing_mapping.raw_json != raw_json
            store.upsert_external_track(
                NAVIDROME_PROVIDER,
                song.id,
                track_id,
                raw_json=raw_json,
            )
            if existing_mapping is None:
                imported_count += 1
            elif changed or raw_changed:
                updated_count += 1
        except Exception:
            failed_count += 1
            logger.exception("Failed to sync Navidrome song item_id=%s", song.id)

    stale_count = 0
    if mark_stale and limit is None:
        stale_external_ids = existing_external_ids - seen_external_ids
        for external_id in stale_external_ids:
            mapping = store.get_external_track(NAVIDROME_PROVIDER, external_id)
            if mapping is None:
                continue
            store.mark_track_missing(mapping.track_id)
            stale_count += 1

    external_id_count = store.count_external_tracks(NAVIDROME_PROVIDER)
    tracks_without_external_id = max(store.count_tracks() - external_id_count, 0)
    result = NavidromeSyncResult(
        seen_count=len(seen_external_ids),
        imported_count=imported_count,
        updated_count=updated_count,
        stale_count=stale_count,
        failed_count=failed_count,
        external_id_count=external_id_count,
        tracks_without_external_id=tracks_without_external_id,
    )
    logger.info("Finished Navidrome sync %s", result.summary())
    return result


def _song_to_scanned_track(song: NavidromeSong) -> ScannedTrack:
    return ScannedTrack(
        path=f"navidrome://{song.id}",  # type: ignore[arg-type]
        artist=song.artist,
        title=song.title,
        album=song.album,
        genre=song.genre,
        year=song.year,
        duration=float(song.duration) if song.duration is not None else None,
        file_size=song.size or 0,
        mtime=0,
    )
