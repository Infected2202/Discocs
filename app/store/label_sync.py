"""Store Label Sync domain: доступы к внешним сервисам, состояние синхронизации лейблов, штрихкоды.

Логика синхронизации — ``app/services/label_sync``; здесь только данные.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass

from app.models import utc_now
from app.store.labels import EDITORIAL_SOURCE

_CHUNK = 500

LABEL_SYNC_FOUND = "found"
LABEL_SYNC_NOT_FOUND = "not_found"
LABEL_SYNC_ERROR = "error"
# Релиз без лейбла («123456 Records DK», «Independent», Discogs: «Not On Label») — искать нечего.
LABEL_SYNC_SELF_RELEASED = "self_released"


@dataclass(frozen=True)
class LabelSyncCandidate:
    label_id: int
    name: str
    status: str | None
    keys_hash: str | None


@dataclass(frozen=True)
class LabelTrackSource:
    label_id: int
    release_id: int
    release_track_count: int
    track_id: int
    path: str | None
    isrc: tuple[str, ...]


def _isrcs(raw_json: str | None) -> tuple[str, ...]:
    if not raw_json:
        return ()
    try:
        value = json.loads(raw_json).get("isrc")
    except (ValueError, AttributeError):
        return ()
    if isinstance(value, str):
        value = [value]
    return tuple(str(code).strip().upper() for code in value or () if str(code).strip())


def _raw_path(raw_json: str | None) -> str | None:
    if not raw_json:
        return None
    try:
        path = json.loads(raw_json).get("path")
    except (ValueError, AttributeError):
        return None
    return str(path) if path else None


class LabelSyncStoreMixin:
    # ---------- доступы ----------

    def get_integration_secret(self, name: str) -> str | None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            row = conn.execute("SELECT value FROM integration_secrets WHERE name = ?", (name,)).fetchone()
        return str(row[0]) if row else None

    def set_integration_secret(self, name: str, value: str) -> None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                """
                INSERT INTO integration_secrets (name, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (name, value, utc_now()),
            )

    def delete_integration_secret(self, name: str) -> None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.execute("DELETE FROM integration_secrets WHERE name = ?", (name,))

    # ---------- состояние лейблов ----------

    def seed_label_sync_state(self) -> int:
        """Лейблы, уже заполненные раньше (tools/label-sync), отметить обработанными.

        Ненайденные получают ``keys_hash`` NULL: первый прогон только запомнит их текущие
        штрихкоды, а искать заново будет, когда появятся новые.
        """
        with self.connect() as conn:  # type: ignore[attr-defined]
            cursor = conn.execute(
                """
                INSERT INTO label_sync_state (label_id, status, keys_hash, attempted_at)
                SELECT l.id,
                       CASE WHEN COALESCE(l.external_ids_json, '{}') NOT IN ('{}', '') THEN ? ELSE ? END,
                       NULL, l.metadata_synced_at
                FROM labels l
                WHERE l.metadata_synced_at IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM label_sync_state s WHERE s.label_id = l.id)
                """,
                (LABEL_SYNC_FOUND, LABEL_SYNC_NOT_FOUND),
            )
            return int(cursor.rowcount or 0)

    def label_sync_candidates(self) -> list[LabelSyncCandidate]:
        """Лейблы с живыми релизами и их состояние синхронизации, крупные первыми."""
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                """
                SELECT l.id, l.name, s.status, s.keys_hash, COUNT(DISTINCT rl.release_id) AS releases
                FROM labels l
                JOIN release_labels rl ON rl.label_id = l.id
                LEFT JOIN label_sync_state s ON s.label_id = l.id
                WHERE EXISTS (
                    SELECT 1 FROM release_tracks rt JOIN tracks t ON t.id = rt.track_id
                    WHERE rt.release_id = rl.release_id AND t.missing_at IS NULL)
                GROUP BY l.id
                ORDER BY releases DESC, l.name COLLATE NOCASE
                """
            ).fetchall()
        return [LabelSyncCandidate(int(r[0]), str(r[1]), r[2], r[3]) for r in rows]

    def label_track_sources(self, label_ids: Iterable[int]) -> list[LabelTrackSource]:
        """Доступные треки лейблов: путь файла и ISRC — из данных Navidrome."""
        ids = list(dict.fromkeys(int(i) for i in label_ids))
        out: list[LabelTrackSource] = []
        with self.connect() as conn:  # type: ignore[attr-defined]
            for start in range(0, len(ids), _CHUNK):
                chunk = ids[start:start + _CHUNK]
                marks = ", ".join("?" for _ in chunk)
                for r in conn.execute(
                    f"""
                    SELECT rl.label_id, rl.release_id,
                           (SELECT COUNT(*) FROM release_tracks x WHERE x.release_id = rl.release_id) AS size,
                           t.id, et.raw_json
                    FROM release_labels rl
                    JOIN release_tracks rt ON rt.release_id = rl.release_id
                    JOIN tracks t ON t.id = rt.track_id AND t.missing_at IS NULL
                    LEFT JOIN external_tracks et ON et.track_id = t.id AND et.provider = 'navidrome'
                    WHERE rl.label_id IN ({marks})
                    ORDER BY rl.label_id, rl.release_id, rt.disc_number, rt.track_number, t.id
                    """,
                    chunk,
                ):
                    out.append(LabelTrackSource(
                        label_id=int(r[0]), release_id=int(r[1]), release_track_count=int(r[2]),
                        track_id=int(r[3]), path=_raw_path(r[4]), isrc=_isrcs(r[4]),
                    ))
        return out

    def set_label_sync_state(
        self,
        label_id: int,
        status: str,
        *,
        keys_hash: str | None,
        beatport_id: str | None = None,
        discogs_id: str | None = None,
        error: str | None = None,
    ) -> None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                """
                INSERT INTO label_sync_state
                    (label_id, status, keys_hash, beatport_id, discogs_id, error, attempted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(label_id) DO UPDATE SET
                    status = excluded.status, keys_hash = excluded.keys_hash,
                    beatport_id = excluded.beatport_id, discogs_id = excluded.discogs_id,
                    error = excluded.error, attempted_at = excluded.attempted_at
                """,
                (label_id, status, keys_hash, beatport_id, discogs_id, error, utc_now()),
            )

    def set_label_sync_keys_hash(self, label_id: int, keys_hash: str) -> None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.execute("UPDATE label_sync_state SET keys_hash = ? WHERE label_id = ?", (keys_hash, label_id))

    def label_sync_counts(self) -> dict[str, int]:
        """Сколько живых лейблов найдено / не найдено / самиздат / с ошибкой / ещё не обрабатывалось."""
        counts = {LABEL_SYNC_FOUND: 0, LABEL_SYNC_NOT_FOUND: 0, LABEL_SYNC_SELF_RELEASED: 0, LABEL_SYNC_ERROR: 0,
                  "pending": 0}
        for candidate in self.label_sync_candidates():
            counts[candidate.status or "pending"] = counts.get(candidate.status or "pending", 0) + 1
        return counts

    def label_external_ids(self, label_ids: Iterable[int]) -> dict[int, dict[str, str]]:
        """Сохранённые привязки лейблов к Beatport/Discogs (``labels.external_ids_json``)."""
        ids = list(dict.fromkeys(int(i) for i in label_ids))
        out: dict[int, dict[str, str]] = {}
        with self.connect() as conn:  # type: ignore[attr-defined]
            for start in range(0, len(ids), _CHUNK):
                chunk = ids[start:start + _CHUNK]
                rows = conn.execute(
                    f"SELECT id, external_ids_json FROM labels WHERE id IN ({','.join('?' * len(chunk))})", chunk,
                ).fetchall()
                for label_id, raw in rows:
                    try:
                        value = json.loads(raw or "{}")
                    except ValueError:
                        value = {}
                    out[int(label_id)] = {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}
        return out

    def labels_without_official_name(self) -> list[tuple[int, dict[str, str]]]:
        """Найденные лейблы, чьё официальное название ещё не запомнено: (id, внешние id)."""
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                """
                SELECT l.id, l.external_ids_json FROM labels l
                JOIN label_sync_state s ON s.label_id = l.id
                WHERE s.status = ? AND l.official_name IS NULL
                ORDER BY l.id
                """,
                (LABEL_SYNC_FOUND,),
            ).fetchall()
        out = []
        for label_id, raw in rows:
            try:
                value = json.loads(raw or "{}")
            except ValueError:
                value = {}
            ids = {str(k): str(v) for k, v in value.items() if v} if isinstance(value, dict) else {}
            if ids.get("beatport") or ids.get("discogs"):
                out.append((int(label_id), ids))
        return out

    def set_label_official_name(self, label_id: int, name: str) -> None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.execute("UPDATE labels SET official_name = ? WHERE id = ?", (name, label_id))

    def clear_label_match(self, label_id: int) -> None:
        """Забыть неверную привязку: ссылки, внешние id, картинку и описание (кроме написанного вручную)."""
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                """
                UPDATE labels
                SET links_json = '[]', external_ids_json = '{}', image_path = NULL, image_source = NULL,
                    official_name = NULL,
                    description = CASE WHEN description_source = ? THEN description END,
                    description_source = CASE WHEN description_source = ? THEN description_source END,
                    updated_at = ?
                WHERE id = ?
                """,
                (EDITORIAL_SOURCE, EDITORIAL_SOURCE, utc_now(), label_id),
            )
            conn.execute(
                "UPDATE label_sync_state SET beatport_id = NULL, discogs_id = NULL WHERE label_id = ?", (label_id,),
            )

    def label_sync_not_found(self, limit: int = 200) -> list[str]:
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                """
                SELECT l.name FROM label_sync_state s JOIN labels l ON l.id = s.label_id
                WHERE s.status = ? ORDER BY l.name COLLATE NOCASE LIMIT ?
                """,
                (LABEL_SYNC_NOT_FOUND, limit),
            ).fetchall()
        return [str(r[0]) for r in rows]

    # ---------- прогоны ----------

    def start_label_sync_run(self, mode: str) -> int:
        with self.connect() as conn:  # type: ignore[attr-defined]
            cursor = conn.execute(
                "INSERT INTO label_sync_runs (mode, status, started_at) VALUES (?, 'running', ?)", (mode, utc_now()),
            )
            return int(cursor.lastrowid)

    def finish_label_sync_run(self, run_id: int, status: str, message: str) -> None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                "UPDATE label_sync_runs SET status = ?, message = ?, finished_at = ? WHERE id = ?",
                (status, message, utc_now(), run_id),
            )

    def last_label_sync_run(self) -> dict[str, str | None] | None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            row = conn.execute(
                "SELECT mode, status, message, started_at, finished_at FROM label_sync_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return {"mode": row[0], "status": row[1], "message": row[2], "started_at": row[3], "finished_at": row[4]}

    # ---------- штрихкоды ----------

    def cached_barcodes(self, track_ids: Iterable[int]) -> dict[int, tuple[str | None, int | None, int | None]]:
        ids = list(dict.fromkeys(int(i) for i in track_ids))
        out: dict[int, tuple[str | None, int | None, int | None]] = {}
        with self.connect() as conn:  # type: ignore[attr-defined]
            for start in range(0, len(ids), _CHUNK):
                chunk = ids[start:start + _CHUNK]
                marks = ", ".join("?" for _ in chunk)
                for r in conn.execute(
                    f"SELECT track_id, barcode, file_size, file_mtime FROM track_barcodes WHERE track_id IN ({marks})",
                    chunk,
                ):
                    out[int(r[0])] = (r[1], r[2], r[3])
        return out

    def save_barcodes(self, rows: Iterable[tuple[int, str | None, int | None, int | None]]) -> None:
        now = utc_now()
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.executemany(
                """
                INSERT INTO track_barcodes (track_id, barcode, file_size, file_mtime, read_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(track_id) DO UPDATE SET
                    barcode = excluded.barcode, file_size = excluded.file_size,
                    file_mtime = excluded.file_mtime, read_at = excluded.read_at
                """,
                [(track_id, barcode, size, mtime, now) for track_id, barcode, size, mtime in rows],
            )
