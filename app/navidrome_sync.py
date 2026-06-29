from __future__ import annotations

from dataclasses import dataclass, replace
import json
import logging
import sqlite3
from typing import Callable

from app.navidrome import NavidromeClient, NavidromeSong
from app.library import envelope_from_navidrome_song, explicit_release_type
from app.scanner import ScannedTrack
from app.store import Store, utc_now


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
    page_size: int = 2000,
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
    album_release_types = _fetch_album_release_types(client)
    logger.info("Fetched release types for %d albums", len(album_release_types))

    songs: list[NavidromeSong] = []
    for song in client.iter_songs(page_size=page_size, limit=limit):
        if progress is not None:
            progress(len(songs) + 1, song)
        songs.append(song)

    seen_external_ids: set[str] = set()
    imported_count = 0
    updated_count = 0
    failed_count = 0
    stale_count = 0

    with store.connect() as conn:
        existing_mappings = _load_existing_navidrome_mappings(conn)
        existing_external_ids = set(existing_mappings)

        for song in songs:
            try:
                if not song.id:
                    failed_count += 1
                    logger.warning("Skipping Navidrome song without id raw=%s", song.raw)
                    continue
                seen_external_ids.add(song.id)
                song = _inject_album_release_type(song, album_release_types)
                raw_json = _song_raw_json(song)
                existing_mapping = existing_mappings.get(song.id)
                scanned = _song_to_scanned_track(song)
                if existing_mapping is not None:
                    track_id = int(existing_mapping["track_id"])
                    changed = False
                    mapped_path = existing_mapping["path"]
                    if mapped_path is None:
                        raise ValueError(f"Track not found: {track_id}")
                    if str(mapped_path).startswith("navidrome://"):
                        raw_changed = existing_mapping["raw_json"] != raw_json
                        if not raw_changed and _track_matches(existing_mapping, scanned):
                            _mark_track_available(conn, track_id)
                            store._upsert_normalized_track_sidecars(
                                conn,
                                track_id,
                                envelope_from_navidrome_song(song, raw_json),
                            )
                            _sync_song_play_state(store, conn, track_id, song)
                            continue
                        track_id, changed = _upsert_track(conn, scanned)
                    elif existing_mapping["raw_json"] == raw_json:
                        _mark_track_available(conn, track_id)
                        store._upsert_normalized_track_sidecars(
                            conn,
                            track_id,
                            envelope_from_navidrome_song(song, raw_json),
                        )
                        _sync_song_play_state(store, conn, track_id, song)
                        continue
                else:
                    track_id, changed = _upsert_track(conn, scanned)

                raw_changed = existing_mapping is not None and existing_mapping["raw_json"] != raw_json
                _upsert_external_track(conn, song.id, track_id, raw_json)
                store._upsert_normalized_track_sidecars(
                    conn,
                    track_id,
                    envelope_from_navidrome_song(song, raw_json),
                )
                _sync_song_play_state(store, conn, track_id, song)
                if existing_mapping is None:
                    imported_count += 1
                elif changed or raw_changed:
                    updated_count += 1
            except Exception:
                failed_count += 1
                logger.exception("Failed to sync Navidrome song item_id=%s", song.id)

        if mark_stale and limit is None:
            stale_external_ids = existing_external_ids - seen_external_ids
            for external_id in stale_external_ids:
                mapping = existing_mappings.get(external_id)
                if mapping is None:
                    continue
                _mark_track_missing(conn, int(mapping["track_id"]))
                stale_count += 1

        external_id_count = _count_external_tracks(conn)
        tracks_without_external_id = max(_count_tracks(conn) - external_id_count, 0)
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


def _sync_song_play_state(
    store: Store,
    conn: sqlite3.Connection,
    track_id: int,
    song: NavidromeSong,
) -> None:
    store._import_external_track_play_state(
        conn,
        track_id,
        play_count=song.play_count,
        last_played_at=song.last_played_at,
        liked=True if song.starred_at else None,
    )


def _fetch_album_release_types(client: NavidromeClient) -> dict[str, str]:
    """Fetch releaseTypes for all albums via getAlbumList2 (paginated bulk fetch)."""
    result: dict[str, str] = {}
    try:
        for album in client.iter_albums():
            album_id = str(album.get("id") or "")
            if not album_id:
                continue
            release_types = album.get("releaseTypes") or []
            if isinstance(release_types, list) and release_types:
                raw_type = str(release_types[0])
            else:
                raw_type = str(album.get("releaseType") or "")
            release_type = explicit_release_type(raw_type)
            if release_type != "unknown":
                result[album_id] = release_type
    except Exception:
        logger.warning("Failed to fetch album list for release types; proceeding without")
    return result


def _inject_album_release_type(song: NavidromeSong, album_release_types: dict[str, str]) -> NavidromeSong:
    """Inject releaseType into song.raw from the pre-fetched album map."""
    if not song.raw or not album_release_types:
        return song
    album_id = str(song.raw.get("albumId") or "")
    if not album_id or album_id not in album_release_types:
        return song
    return replace(song, raw={**song.raw, "releaseType": album_release_types[album_id]})


def _song_to_scanned_track(song: NavidromeSong) -> ScannedTrack:
    return ScannedTrack(
        path=f"navidrome://{song.id}",  # type: ignore[arg-type]
        artist=song.artist,
        title=song.title,
        album=song.album,
        genre=song.genre,
        year=song.year,
        duration=float(song.duration) if song.duration is not None else None,
        album_artist=(song.raw or {}).get("albumArtist") if song.raw else None,
        track_number=_optional_int((song.raw or {}).get("track")) if song.raw else None,
        disc_number=_optional_int((song.raw or {}).get("discNumber")) if song.raw else None,
        total_tracks=_optional_int((song.raw or {}).get("totalTracks")) if song.raw else None,
        file_size=song.size or 0,
        mtime=0,
    )


def _song_raw_json(song: NavidromeSong) -> str:
    return json.dumps(_normalize_raw_json(song.raw or {}), sort_keys=True)


def _normalize_raw_json(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _normalize_raw_json(item) for key, item in value.items()}
    if isinstance(value, list):
        normalized = [_normalize_raw_json(item) for item in value]
        if all(isinstance(item, dict) and "role" in item and "artist" in item for item in normalized):
            return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True))
        return normalized
    return value


def _load_existing_navidrome_mappings(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT
            e.external_id,
            e.track_id,
            e.raw_json,
            t.path,
            t.artist,
            t.title,
            t.album,
            t.genre,
            t.year,
            t.duration,
            t.file_size,
            t.mtime
        FROM external_tracks e
        LEFT JOIN tracks t ON t.id = e.track_id
        WHERE e.provider = ?
        """,
        (NAVIDROME_PROVIDER,),
    ).fetchall()
    return {str(row["external_id"]): row for row in rows}


def _track_matches(row: sqlite3.Row, scanned: ScannedTrack) -> bool:
    return (
        row["path"] == str(scanned.path)
        and row["artist"] == scanned.artist
        and row["title"] == scanned.title
        and row["album"] == scanned.album
        and row["genre"] == scanned.genre
        and row["year"] == scanned.year
        and _optional_float(row["duration"]) == scanned.duration
        and int(row["file_size"]) == scanned.file_size
        and int(row["mtime"]) == scanned.mtime
    )


def _optional_float(value: object) -> float | None:
    return None if value is None else float(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).split("/", 1)[0].strip()
    try:
        return int(text)
    except ValueError:
        return None


def _upsert_track(conn: sqlite3.Connection, scanned: ScannedTrack) -> tuple[int, bool]:
    now = utc_now()
    path = str(scanned.path)
    existing = conn.execute(
        "SELECT id, file_size, mtime FROM tracks WHERE path = ?",
        (path,),
    ).fetchone()
    if existing:
        changed = (
            int(existing["file_size"]) != scanned.file_size
            or int(existing["mtime"]) != scanned.mtime
        )
        conn.execute(
            """
            UPDATE tracks
            SET artist = ?, title = ?, album = ?, genre = ?, year = ?, duration = ?,
                file_size = ?, mtime = ?, missing_at = NULL, last_seen_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                scanned.artist,
                scanned.title,
                scanned.album,
                scanned.genre,
                scanned.year,
                scanned.duration,
                scanned.file_size,
                scanned.mtime,
                now,
                now,
                existing["id"],
            ),
        )
        if changed:
            track_id = int(existing["id"])
            logger.info("Track changed, invalidating derived data track_id=%s path=%s", track_id, path)
            conn.execute("DELETE FROM embeddings WHERE track_id = ?", (track_id,))
            conn.execute("DELETE FROM track_model_outputs WHERE track_id = ?", (track_id,))
            conn.execute("DELETE FROM track_predictions WHERE track_id = ?", (track_id,))
            conn.execute("DELETE FROM track_features WHERE track_id = ?", (track_id,))
        return int(existing["id"]), changed

    cursor = conn.execute(
        """
        INSERT INTO tracks (
            path, artist, title, album, genre, year, duration, file_size, mtime,
            missing_at, last_seen_at, added_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
        """,
        (
            path,
            scanned.artist,
            scanned.title,
            scanned.album,
            scanned.genre,
            scanned.year,
            scanned.duration,
            scanned.file_size,
            scanned.mtime,
            now,
            now,
            now,
            now,
        ),
    )
    return int(cursor.lastrowid), True


def _upsert_external_track(
    conn: sqlite3.Connection,
    external_id: str,
    track_id: int,
    raw_json: str | None,
) -> None:
    track_exists = conn.execute("SELECT 1 FROM tracks WHERE id = ?", (track_id,)).fetchone()
    if track_exists is None:
        raise ValueError(f"Track not found: {track_id}")
    synced_at = utc_now()
    conn.execute(
        """
        DELETE FROM external_tracks
        WHERE provider = ? AND track_id = ? AND external_id != ?
        """,
        (NAVIDROME_PROVIDER, track_id, external_id),
    )
    conn.execute(
        """
        INSERT INTO external_tracks (
            provider, external_id, track_id, raw_json, synced_at
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(provider, external_id) DO UPDATE SET
            track_id = excluded.track_id,
            raw_json = excluded.raw_json,
            synced_at = excluded.synced_at
        """,
        (NAVIDROME_PROVIDER, external_id, track_id, raw_json, synced_at),
    )


def _mark_track_missing(conn: sqlite3.Connection, track_id: int) -> None:
    now = utc_now()
    conn.execute(
        "UPDATE tracks SET missing_at = ?, updated_at = ? WHERE id = ?",
        (now, now, track_id),
    )


def _mark_track_available(conn: sqlite3.Connection, track_id: int) -> None:
    now = utc_now()
    conn.execute(
        "UPDATE tracks SET missing_at = NULL, last_seen_at = ?, updated_at = ? WHERE id = ?",
        (now, now, track_id),
    )


def _count_external_tracks(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM external_tracks WHERE provider = ?",
            (NAVIDROME_PROVIDER,),
        ).fetchone()[0]
    )


def _count_tracks(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0])
