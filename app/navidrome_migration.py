from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import sqlite3
from typing import Any, Callable, Iterable

from app.navidrome import NavidromeSong
from app.store import Store, utc_now


NAVIDROME_PROVIDER = "navidrome"
NAVIDROME_PATH_PREFIX = "navidrome://"
logger = logging.getLogger(__name__)
ProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True)
class NavidromeDuplicateMigrationResult:
    candidates: int
    matched: int
    ambiguous: int
    unmatched: int
    remapped: int
    deleted_duplicates: int
    dry_run: bool

    def summary(self) -> str:
        mode = "dry_run" if self.dry_run else "applied"
        return (
            f"{mode}=true candidates={self.candidates} matched={self.matched} "
            f"ambiguous={self.ambiguous} unmatched={self.unmatched} "
            f"remapped={self.remapped} deleted_duplicates={self.deleted_duplicates}"
        )


def migrate_navidrome_duplicate_tracks(
    store: Store,
    *,
    songs: Iterable[NavidromeSong] | None = None,
    dry_run: bool = True,
    batch_size: int = 500,
    progress: ProgressCallback | None = None,
) -> NavidromeDuplicateMigrationResult:
    """Move Navidrome external IDs from navidrome:// duplicate rows to existing local rows."""
    with store.connect() as conn:
        local_index = local_track_suffix_index(conn)
        rows = _live_duplicate_rows(conn, songs) if songs is not None else _stored_duplicate_rows(conn)

        matched: list[tuple[DuplicateCandidate, int]] = []
        ambiguous = 0
        unmatched = 0
        for row in rows:
            if not row.real_path:
                unmatched += 1
                continue
            matches = [track_id for track_id in local_index.get(normalize_relative_path(row.real_path), []) if track_id != row.duplicate_track_id]
            if len(matches) == 1:
                matched.append((row, matches[0]))
            elif len(matches) > 1:
                ambiguous += 1
            else:
                unmatched += 1

    remapped = 0
    deleted_duplicates = 0
    if not dry_run and matched:
        remapped, deleted_duplicates = _apply_matches(
            store,
            matched,
            batch_size=max(int(batch_size), 1),
            progress=progress,
        )

    result = NavidromeDuplicateMigrationResult(
        candidates=len(rows),
        matched=len(matched),
        ambiguous=ambiguous,
        unmatched=unmatched,
        remapped=remapped,
        deleted_duplicates=deleted_duplicates,
        dry_run=dry_run,
    )
    logger.info("Navidrome duplicate migration %s", result.summary())
    return result


@dataclass(frozen=True)
class DuplicateCandidate:
    external_id: str
    duplicate_track_id: int
    real_path: str | None


def _stored_duplicate_rows(conn: sqlite3.Connection) -> list[DuplicateCandidate]:
    rows = conn.execute(
        """
        SELECT e.external_id, e.track_id AS duplicate_track_id, e.raw_json
        FROM external_tracks e
        JOIN tracks t ON t.id = e.track_id
        WHERE e.provider = ?
          AND t.path LIKE ?
        ORDER BY e.external_id
        """,
        (NAVIDROME_PROVIDER, f"{NAVIDROME_PATH_PREFIX}%"),
    ).fetchall()
    return [
        DuplicateCandidate(
            external_id=str(row["external_id"]),
            duplicate_track_id=int(row["duplicate_track_id"]),
            real_path=_navidrome_raw_path(_load_raw_json(row["raw_json"])),
        )
        for row in rows
    ]


def _live_duplicate_rows(conn: sqlite3.Connection, songs: Iterable[NavidromeSong]) -> list[DuplicateCandidate]:
    duplicate_track_ids = {
        str(row["external_id"]): int(row["duplicate_track_id"])
        for row in conn.execute(
            """
            SELECT e.external_id, e.track_id AS duplicate_track_id
            FROM external_tracks e
            JOIN tracks t ON t.id = e.track_id
            WHERE e.provider = ?
              AND t.path LIKE ?
            """,
            (NAVIDROME_PROVIDER, f"{NAVIDROME_PATH_PREFIX}%"),
        ).fetchall()
    }
    candidates: list[DuplicateCandidate] = []
    for song in songs:
        if not song.id:
            continue
        duplicate_track_id = duplicate_track_ids.get(song.id)
        if duplicate_track_id is None:
            continue
        candidates.append(
            DuplicateCandidate(
                external_id=song.id,
                duplicate_track_id=duplicate_track_id,
                real_path=_navidrome_raw_path(song.raw or {}),
            )
        )
    return candidates


def _apply_matches(
    store: Store,
    matched: list[tuple[DuplicateCandidate, int]],
    *,
    batch_size: int,
    progress: ProgressCallback | None,
) -> tuple[int, int]:
    remapped = 0
    deleted_duplicates = 0
    total = len(matched)
    for start in range(0, total, batch_size):
        batch = matched[start : start + batch_size]
        now = utc_now()
        with store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for row, target_track_id in batch:
                    conn.execute(
                        """
                        DELETE FROM external_tracks
                        WHERE provider = ? AND track_id = ? AND external_id != ?
                        """,
                        (NAVIDROME_PROVIDER, target_track_id, row.external_id),
                    )
                    conn.execute(
                        """
                        UPDATE external_tracks
                        SET track_id = ?, synced_at = ?
                        WHERE provider = ? AND external_id = ?
                        """,
                        (target_track_id, now, NAVIDROME_PROVIDER, row.external_id),
                    )
                    remapped += 1
                    cursor = conn.execute(
                        """
                        DELETE FROM tracks
                        WHERE id = ?
                          AND path LIKE ?
                        """,
                        (row.duplicate_track_id, f"{NAVIDROME_PATH_PREFIX}%"),
                    )
                    deleted_duplicates += int(cursor.rowcount)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        if progress is not None:
            progress(min(start + len(batch), total), total)
    return remapped, deleted_duplicates


def local_track_suffix_index(conn: sqlite3.Connection) -> dict[str, list[int]]:
    rows = conn.execute(
        """
        SELECT id, path, file_size
        FROM tracks
        WHERE path NOT LIKE ?
        """,
        (f"{NAVIDROME_PATH_PREFIX}%",),
    ).fetchall()
    index: dict[str, list[int]] = {}
    for row in rows:
        track_id = int(row["id"])
        normalized = normalize_absolute_path(str(row["path"]))
        parts = [part for part in normalized.split("/") if part]
        for start in range(len(parts)):
            suffix = "/".join(parts[start:])
            if not suffix:
                continue
            index.setdefault(suffix, []).append(track_id)
    return index


def _load_raw_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _navidrome_raw_path(raw: dict[str, Any]) -> str | None:
    path = raw.get("path")
    return str(path) if path else None


def normalize_relative_path(path: str) -> str:
    normalized = normalize_absolute_path(path)
    return normalized.lstrip("/")


def normalize_absolute_path(path: str) -> str:
    return path.replace("\\", "/").strip().casefold()
