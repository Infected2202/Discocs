"""Store Labels domain: record labels, release↔label links, label metadata.

Part of the app/store package.
Do not import this module directly; use app.store instead.
"""
from __future__ import annotations

import json
import random
import sqlite3

from app.library import clean_display_text, normalize_text
from app.models import Label, LabelMetadata, ReleaseSummaryRow, utc_now
from app.store._helpers import _discography_group_key, row_to_release

# Порядок групп на странице лейбла — как в дискографии артиста; «releases» —
# всё, чей тип не альбом/EP/сингл/сборник (саундтреки, миксы, неизвестный тип).
LABEL_RELEASE_GROUPS = ("albums", "eps", "singles", "compilations", "releases")
# Описание, написанное вручную (PUT /labels/{id}/description), — tools/label-sync его не перезаписывает.
EDITORIAL_SOURCE = "editorial"

# Релиз «живой», если у него есть хоть один доступный трек — как в полках дашборда.
_AVAILABLE_RELEASE = """
    EXISTS (
        SELECT 1
        FROM release_tracks rt
        JOIN tracks t ON t.id = rt.track_id
        WHERE rt.release_id = rl.release_id AND t.missing_at IS NULL
    )
"""


def row_to_label(row: sqlite3.Row) -> Label:
    keys = row.keys()
    return Label(
        id=int(row["id"]),
        name=str(row["name"]),
        normalized_name=str(row["normalized_name"]),
        image_path=row["image_path"],
        image_source=row["image_source"],
        description=row["description"],
        description_source=row["description_source"],
        links=_json_list(row["links_json"]),
        external_ids=_json_dict(row["external_ids_json"]),
        metadata_synced_at=row["metadata_synced_at"],
        release_count=int(row["release_count"] or 0) if "release_count" in keys else 0,
        liked=bool(row["liked"]) if "liked" in keys else False,
    )


def release_sort_key(row: ReleaseSummaryRow) -> tuple[int, int, int]:
    """(год, месяц, день) релиза; неизвестные части — 0.

    Дата бывает только годом — такие релизы стоят по году, а внутри года их
    упорядочивает название (см. ``sort_label_releases``).
    """
    release = row.release
    parts = (release.release_date or "").split("-")
    year = _int_or_zero(parts[0]) or (release.release_year or 0)
    month = _int_or_zero(parts[1]) if len(parts) > 1 else 0
    day = _int_or_zero(parts[2]) if len(parts) > 2 else 0
    return year, month, day


def sort_label_releases(rows: list[ReleaseSummaryRow], *, newest_first: bool) -> list[ReleaseSummaryRow]:
    sign = -1 if newest_first else 1

    def key(row: ReleaseSummaryRow) -> tuple[bool, int, int, int, str, int]:
        year, month, day = release_sort_key(row)
        return (
            year == 0,  # без даты — всегда в конце
            sign * year,
            sign * month,
            sign * day,
            row.release.title.casefold(),
            row.release.id,
        )

    return sorted(rows, key=key)


def group_label_releases(rows: list[ReleaseSummaryRow]) -> list[tuple[str, list[ReleaseSummaryRow]]]:
    """Релизы по типу (непустые группы, порядок ``LABEL_RELEASE_GROUPS``); порядок внутри сохраняется."""
    groups: dict[str, list[ReleaseSummaryRow]] = {key: [] for key in LABEL_RELEASE_GROUPS}
    for row in rows:
        groups[_discography_group_key(row.release.release_type, False)].append(row)
    return [(key, items) for key, items in groups.items() if items]


class LabelsStoreMixin:
    def _replace_release_labels(
        self,
        conn: sqlite3.Connection,
        release_id: int,
        names: tuple[str, ...],
        now: str,
    ) -> None:
        label_ids: list[int] = []
        for name in names:
            label_id = self._upsert_label(conn, name, now)
            if label_id not in label_ids:
                label_ids.append(label_id)
        current = [
            int(row["label_id"])
            for row in conn.execute(
                "SELECT label_id FROM release_labels WHERE release_id = ? ORDER BY position",
                (release_id,),
            ).fetchall()
        ]
        if current == label_ids:
            return
        conn.execute("DELETE FROM release_labels WHERE release_id = ?", (release_id,))
        conn.executemany(
            "INSERT INTO release_labels (release_id, label_id, position) VALUES (?, ?, ?)",
            [(release_id, label_id, position) for position, label_id in enumerate(label_ids)],
        )
        conn.execute(
            "UPDATE releases SET label = ?, updated_at = ? WHERE id = ?",
            (" / ".join(names) or None, now, release_id),
        )

    def _upsert_label(self, conn: sqlite3.Connection, name: str, now: str) -> int:
        display_name = clean_display_text(name)
        if not display_name:
            raise ValueError("Label name is empty")
        normalized_name = normalize_text(display_name)
        # Имя первой встречи остаётся: разные релизы пишут один лейбл по-разному
        # («trip recordings» / «Trip Recordings»), перезапись при каждом синке
        # гоняла бы его туда-сюда.
        row = conn.execute(
            "SELECT id FROM labels WHERE normalized_name = ?",
            (normalized_name,),
        ).fetchone()
        if row is not None:
            return int(row["id"])
        cursor = conn.execute(
            """
            INSERT INTO labels (name, normalized_name, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (display_name, normalized_name, now, now),
        )
        return int(cursor.lastrowid)

    def list_labels(self, *, limit: int, offset: int) -> tuple[list[Label], int]:
        """Лейблы с живыми релизами: лайкнутые первыми, дальше — больше релизов выше.

        Лайки — текущего пользователя. У сервисного принципала (label-sync)
        пользователя нет: лайков у него нет, а не ошибка.
        """
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT l.*, COUNT(DISTINCT rl.release_id) AS release_count,
                    COALESCE(MAX(p.liked), 0) AS liked
                FROM labels l
                JOIN release_labels rl ON rl.label_id = l.id
                LEFT JOIN user_label_preferences p
                  ON p.label_id = l.id AND p.user_id = ?
                WHERE {_AVAILABLE_RELEASE}
                GROUP BY l.id
                ORDER BY liked DESC, release_count DESC, l.name COLLATE NOCASE, l.id
                LIMIT ? OFFSET ?
                """,
                (self.user_id, limit, offset),
            ).fetchall()
            total = conn.execute(
                f"""
                SELECT COUNT(DISTINCT rl.label_id)
                FROM release_labels rl
                WHERE {_AVAILABLE_RELEASE}
                """
            ).fetchone()[0]
        return [row_to_label(row) for row in rows], int(total or 0)

    def get_label(self, label_id: int) -> Label | None:
        with self.connect() as conn:
            row = conn.execute(
                f"""
                SELECT l.*,
                    (
                        SELECT COUNT(DISTINCT rl.release_id)
                        FROM release_labels rl
                        WHERE rl.label_id = l.id AND {_AVAILABLE_RELEASE}
                    ) AS release_count,
                    COALESCE(p.liked, 0) AS liked
                FROM labels l
                LEFT JOIN user_label_preferences p
                  ON p.label_id = l.id AND p.user_id = ?
                WHERE l.id = ?
                """,
                (self.user_id, label_id),
            ).fetchone()
        return row_to_label(row) if row is not None else None

    def set_label_liked(self, label_id: int, liked: bool) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO user_label_preferences (user_id, label_id, liked, liked_at, updated_at)
                VALUES (discocs_user_id(), ?, ?, ?, ?)
                ON CONFLICT(user_id, label_id) DO UPDATE SET
                    liked = excluded.liked,
                    liked_at = excluded.liked_at,
                    updated_at = excluded.updated_at
                """,
                (label_id, int(liked), now if liked else None, now),
            )

    def label_releases(self, label_id: int, *, newest_first: bool = True) -> list[ReleaseSummaryRow]:
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT r.*
                FROM release_labels rl
                JOIN releases r ON r.id = rl.release_id
                WHERE rl.label_id = ? AND {_AVAILABLE_RELEASE}
                """,
                (label_id,),
            ).fetchall()
            artists_by_release = self._artists_for_releases(conn, [int(row["id"]) for row in rows])
        releases = [
            ReleaseSummaryRow(row_to_release(row), artists_by_release.get(int(row["id"]), []))
            for row in rows
        ]
        return sort_label_releases(releases, newest_first=newest_first)

    def label_track_ids(self, label_id: int, *, limit: int, shuffle: bool = False) -> list[int]:
        """Доступные треки релизов лейбла для очереди воспроизведения.

        Подряд — релизы от новых к старым, внутри релиза по трек-листу. С
        shuffle — случайная выборка по всему каталогу: у крупного лейбла
        (Suara — тысячи треков) первые ``limit`` подряд были бы одними свежими релизами.
        """
        order = {
            row.release.id: position
            for position, row in enumerate(self.label_releases(label_id, newest_first=True))
        }
        if not order:
            return []
        placeholders = ",".join("?" for _id in order)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT rt.release_id, t.id AS track_id
                FROM release_tracks rt
                JOIN tracks t ON t.id = rt.track_id
                WHERE rt.release_id IN ({placeholders}) AND t.missing_at IS NULL
                ORDER BY rt.disc_number IS NULL, rt.disc_number,
                         rt.track_number IS NULL, rt.track_number, rt.position, t.id
                """,
                list(order),
            ).fetchall()
        ordered = sorted(rows, key=lambda row: order[int(row["release_id"])])
        track_ids = list(dict.fromkeys(int(row["track_id"]) for row in ordered))
        if shuffle and len(track_ids) > limit:
            return random.sample(track_ids, limit)
        return track_ids[:limit]

    def label_id_by_name(self, name: str) -> int | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM labels WHERE normalized_name = ?",
                (normalize_text(name),),
            ).fetchone()
        return int(row["id"]) if row is not None else None

    def labels_for_release(self, release_id: int) -> list[Label]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT l.*
                FROM release_labels rl
                JOIN labels l ON l.id = rl.label_id
                WHERE rl.release_id = ?
                ORDER BY rl.position
                """,
                (release_id,),
            ).fetchall()
        return [row_to_label(row) for row in rows]

    def save_label_metadata(self, metadata: LabelMetadata) -> int:
        """Записать данные из tools/label-sync; лейбл заводится, если его ещё нет.

        Описание, ссылки и внешние id заменяются целиком: скрипт присылает
        запись полностью. Картинка — отдельно (``set_label_image``).
        Описание, написанное вручную (``EDITORIAL_SOURCE``), скрипт не трогает.
        """
        now = utc_now()
        description = clean_description(metadata.description)
        with self.connect() as conn:
            label_id = self._upsert_label(conn, metadata.name, now)
            conn.execute(
                """
                UPDATE labels
                SET description = CASE WHEN description_source = ? THEN description ELSE ? END,
                    description_source = CASE WHEN description_source = ? THEN description_source ELSE ? END,
                    links_json = ?, external_ids_json = ?,
                    metadata_synced_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    EDITORIAL_SOURCE,
                    description,
                    EDITORIAL_SOURCE,
                    metadata.description_source if description else None,
                    json.dumps(metadata.links or [], ensure_ascii=False),
                    json.dumps(metadata.external_ids or {}, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                    label_id,
                ),
            )
        return label_id

    def set_label_description(self, label_id: int, description: str | None) -> None:
        """Описание, написанное вручную; пустое — снова отдаёт описание скрипту."""
        text = clean_description(description)
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE labels SET description = ?, description_source = ?, updated_at = ?
                WHERE id = ?
                """,
                (text, EDITORIAL_SOURCE if text else None, utc_now(), label_id),
            )

    def set_label_image(self, label_id: int, image_path: str, image_source: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE labels SET image_path = ?, image_source = ?, updated_at = ? WHERE id = ?",
                (image_path, image_source, utc_now(), label_id),
            )


def clean_description(value: str | None) -> str | None:
    if value is None:
        return None
    lines = [" ".join(line.split()) for line in str(value).replace("\r\n", "\n").split("\n")]
    text = "\n".join(lines).strip()
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text or None


def _int_or_zero(value: str) -> int:
    return int(value) if value.isdigit() else 0


def _json_list(value: str | None) -> list[dict[str, str]]:
    try:
        decoded = json.loads(value) if value else []
    except (TypeError, json.JSONDecodeError):
        return []
    return [item for item in decoded if isinstance(item, dict)] if isinstance(decoded, list) else []


def _json_dict(value: str | None) -> dict[str, str]:
    try:
        decoded = json.loads(value) if value else {}
    except (TypeError, json.JSONDecodeError):
        return {}
    return {str(k): str(v) for k, v in decoded.items()} if isinstance(decoded, dict) else {}
