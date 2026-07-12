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


@dataclass(frozen=True)
class NavidromeReleaseRepairResult:
    candidates: int
    matched: int
    ambiguous: int
    path_mismatch: int
    merged: int
    dry_run: bool

    def summary(self) -> str:
        mode = "dry_run" if self.dry_run else "applied"
        return (
            f"{mode}=true candidates={self.candidates} matched={self.matched} "
            f"ambiguous={self.ambiguous} path_mismatch={self.path_mismatch} "
            f"merged={self.merged}"
        )


@dataclass(frozen=True)
class ReleaseRepairCandidate:
    old_release_id: int
    target_release_id: int
    old_title: str
    target_title: str
    sample_track_external_id: str
    old_path: str
    current_path: str


def repair_navidrome_empty_releases(
    store: Store,
    *,
    dry_run: bool = True,
    batch_size: int = 250,
    progress: ProgressCallback | None = None,
) -> NavidromeReleaseRepairResult:
    """Merge strongly matched empty Navidrome releases into their successors.

    Historical normalized membership is no longer available after a release
    becomes empty, so repair is intentionally conservative: the same stable
    song ID must connect old and current releases, its source path must be
    unchanged, and the relationship must be one-to-one.
    """
    with store.connect() as conn:
        candidates, ambiguous, path_mismatch = _release_repair_candidates(conn)

    merged = 0
    if not dry_run and candidates:
        merged = _apply_release_repairs(
            store,
            candidates,
            batch_size=max(int(batch_size), 1),
            progress=progress,
        )

    result = NavidromeReleaseRepairResult(
        candidates=len(candidates) + ambiguous + path_mismatch,
        matched=len(candidates),
        ambiguous=ambiguous,
        path_mismatch=path_mismatch,
        merged=merged,
        dry_run=dry_run,
    )
    logger.info("Navidrome empty release repair %s", result.summary())
    return result


def _release_repair_candidates(
    conn: sqlite3.Connection,
) -> tuple[list[ReleaseRepairCandidate], int, int]:
    rows = conn.execute(
        """
        SELECT
            old.id AS old_release_id,
            old.title AS old_title,
            target.id AS target_release_id,
            target.title AS target_title,
            old_external.raw_json AS old_raw_json,
            current_track.external_id AS sample_track_external_id,
            current_track.raw_json AS current_raw_json
        FROM releases old
        JOIN external_ids old_external
          ON old_external.provider = ?
         AND old_external.entity_type = 'release'
         AND old_external.entity_id = old.id
        JOIN external_tracks current_track
          ON current_track.provider = ?
         AND current_track.external_id = json_extract(old_external.raw_json, '$.id')
        JOIN release_tracks current_membership
          ON current_membership.track_id = current_track.track_id
        JOIN releases target
          ON target.id = current_membership.release_id
        WHERE NOT EXISTS (
            SELECT 1 FROM release_tracks old_membership
            WHERE old_membership.release_id = old.id
        )
          AND target.id != old.id
        ORDER BY old.id
        """,
        (NAVIDROME_PROVIDER, NAVIDROME_PROVIDER),
    ).fetchall()

    target_counts: dict[int, int] = {}
    for row in rows:
        target_id = int(row["target_release_id"])
        target_counts[target_id] = target_counts.get(target_id, 0) + 1

    matched: list[ReleaseRepairCandidate] = []
    ambiguous = 0
    path_mismatch = 0
    for row in rows:
        target_id = int(row["target_release_id"])
        if target_counts[target_id] != 1:
            ambiguous += 1
            continue
        old_path = _navidrome_raw_path(_load_raw_json(row["old_raw_json"]))
        current_path = _navidrome_raw_path(_load_raw_json(row["current_raw_json"]))
        if (
            not old_path
            or not current_path
            or normalize_absolute_path(old_path) != normalize_absolute_path(current_path)
        ):
            path_mismatch += 1
            continue
        matched.append(
            ReleaseRepairCandidate(
                old_release_id=int(row["old_release_id"]),
                target_release_id=target_id,
                old_title=str(row["old_title"]),
                target_title=str(row["target_title"]),
                sample_track_external_id=str(row["sample_track_external_id"]),
                old_path=old_path,
                current_path=current_path,
            )
        )
    return matched, ambiguous, path_mismatch


def _apply_release_repairs(
    store: Store,
    matched: list[ReleaseRepairCandidate],
    *,
    batch_size: int,
    progress: ProgressCallback | None,
) -> int:
    merged = 0
    total = len(matched)
    for start in range(0, total, batch_size):
        batch = matched[start : start + batch_size]
        with store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                release_id_map = {
                    candidate.old_release_id: candidate.target_release_id
                    for candidate in batch
                }
                for candidate in batch:
                    _merge_release_preference(
                        conn,
                        candidate.old_release_id,
                        candidate.target_release_id,
                    )
                    conn.execute(
                        "UPDATE playback_events SET release_id = ? WHERE release_id = ?",
                        (candidate.target_release_id, candidate.old_release_id),
                    )
                    conn.execute(
                        """
                        UPDATE playback_sessions
                        SET source_id = ?, source_label = ?
                        WHERE source_type = 'release' AND source_id = ?
                        """,
                        (
                            candidate.target_release_id,
                            candidate.target_title,
                            candidate.old_release_id,
                        ),
                    )
                    conn.execute(
                        """
                        UPDATE queue_items
                        SET source_id = ?
                        WHERE source_type = 'release' AND source_id = ?
                        """,
                        (candidate.target_release_id, candidate.old_release_id),
                    )
                    conn.execute(
                        """
                        UPDATE external_ids
                        SET entity_id = ?
                        WHERE entity_type = 'release' AND entity_id = ?
                        """,
                        (candidate.target_release_id, candidate.old_release_id),
                    )
                    cursor = conn.execute(
                        """
                        DELETE FROM releases
                        WHERE id = ?
                          AND NOT EXISTS (
                              SELECT 1 FROM release_tracks
                              WHERE release_id = ?
                          )
                        """,
                        (candidate.old_release_id, candidate.old_release_id),
                    )
                    merged += int(cursor.rowcount)
                _rewrite_session_release_states(conn, release_id_map)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        if progress is not None:
            progress(min(start + len(batch), total), total)
    return merged


def _merge_release_preference(
    conn: sqlite3.Connection,
    old_release_id: int,
    target_release_id: int,
) -> None:
    old_rows = conn.execute(
        "SELECT * FROM user_release_preferences WHERE release_id = ?",
        (old_release_id,),
    ).fetchall()
    for old in old_rows:
        user_id = int(old["user_id"])
        target = conn.execute(
            "SELECT * FROM user_release_preferences WHERE user_id = ? AND release_id = ?",
            (user_id, target_release_id),
        ).fetchone()
        if target is None:
            conn.execute(
                "UPDATE user_release_preferences SET release_id = ? WHERE user_id = ? AND release_id = ?",
                (target_release_id, user_id, old_release_id),
            )
            continue
        conn.execute(
            """UPDATE user_release_preferences
               SET liked = ?, play_count = ?, completion_count = ?, skip_count = ?,
                   last_played_at = ?, last_completed_at = ?, score = ?, updated_at = ?
               WHERE user_id = ? AND release_id = ?""",
            (
                max(int(old["liked"]), int(target["liked"])),
                int(old["play_count"]) + int(target["play_count"]),
                int(old["completion_count"]) + int(target["completion_count"]),
                int(old["skip_count"]) + int(target["skip_count"]),
                _latest_text(old["last_played_at"], target["last_played_at"]),
                _latest_text(old["last_completed_at"], target["last_completed_at"]),
                float(old["score"]) + float(target["score"]),
                _latest_text(old["updated_at"], target["updated_at"]),
                user_id,
                target_release_id,
            ),
        )
        conn.execute(
            "DELETE FROM user_release_preferences WHERE user_id = ? AND release_id = ?",
            (user_id, old_release_id),
        )


def _rewrite_session_release_states(
    conn: sqlite3.Connection,
    release_id_map: dict[int, int],
) -> None:
    rows = conn.execute(
        "SELECT id, state_json FROM playback_sessions WHERE state_json IS NOT NULL",
    ).fetchall()
    for row in rows:
        try:
            state = json.loads(str(row["state_json"]))
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(state, dict):
            continue
        release_plays = state.get("session_release_plays")
        if not isinstance(release_plays, dict):
            continue
        changed = False
        for old_release_id, target_release_id in release_id_map.items():
            old_key = str(old_release_id)
            if old_key not in release_plays:
                continue
            target_key = str(target_release_id)
            old_value = release_plays.pop(old_key)
            existing = release_plays.get(target_key)
            if isinstance(old_value, (int, float)) and isinstance(existing, (int, float)):
                release_plays[target_key] = old_value + existing
            elif existing is None:
                release_plays[target_key] = old_value
            changed = True
        if not changed:
            continue
        conn.execute(
            "UPDATE playback_sessions SET state_json = ? WHERE id = ?",
            (json.dumps(state, sort_keys=True), row["id"]),
        )


def _latest_text(first: object, second: object) -> str | None:
    values = [str(value) for value in (first, second) if value is not None]
    return max(values) if values else None


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
