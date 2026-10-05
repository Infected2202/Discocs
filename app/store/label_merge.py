"""Склейка лейблов: дубли из тегов и разные записи одного лейбла сливаются в один.

Part of the app/store package. Do not import this module directly; use
app.store instead.

Группа — лейблы с общим ключом склейки (``merge_key`` названия без юридической обёртки;
у строки из нескольких лейблов «A / B» — ключ того, у кого больше релизов, а найденное
у самой пары в склейку не идёт), с общим id Beatport или Discogs или с ключом официального
названия. Основной в группе — найденный
синхронизацией лейблов, затем с бо́льшим числом релизов: ему переходят релизы и лайки, а
картинка, описание, ссылки и внешние id — если своих нет. Имя — официальное (Beatport, иначе
Discogs), у ненайденных — очищенное название самого крупного. Ненайденный лейбл, сменивший
имя, ищется снова. Все ключи группы пишутся в ``label_aliases``: следующий скан кладёт релиз
с любым из этих написаний сразу в основной лейбл.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.label_names import label_parts, merge_key, pick_part, strip_legal
from app.library import normalize_text
from app.models import utc_now

logger = logging.getLogger(__name__)

# Статусы label_sync_state (app/store/label_sync.py) — здесь только чтение.
_FOUND = "found"
_RETRY_WHEN_RENAMED = ("not_found", "error", "self_released")


@dataclass(frozen=True)
class LabelMergeSummary:
    merged: int = 0
    renamed: int = 0


@dataclass(frozen=True)
class _Label:
    id: int
    name: str
    official_name: str | None
    external_ids: dict[str, str]
    status: str | None
    releases: int


def merge_labels(conn: sqlite3.Connection) -> LabelMergeSummary:
    """Слить дубли, переименовать по официальному названию, пересобрать ``label_aliases``."""
    labels = [
        _Label(int(r[0]), str(r[1]), r[2], _ids(r[3]), r[4], int(r[5]))
        for r in conn.execute(
            """
            SELECT l.id, l.name, l.official_name, l.external_ids_json, s.status,
                   (SELECT COUNT(*) FROM release_labels rl WHERE rl.label_id = l.id)
            FROM labels l LEFT JOIN label_sync_state s ON s.label_id = l.id
            """
        )
    ]
    by_id = {label.id: label for label in labels}
    effective = _effective_names(labels)
    # «OWSLA/Atlantic» ушёл к Atlantic: его находка (Beatport держит такие пары отдельными
    # лейблами) — про пару, а не про Atlantic, поэтому в склейке не участвует.
    split = {label.id for label in labels if merge_key(effective[label.id]) != merge_key(strip_legal(label.name))}

    parent = {label.id: label.id for label in labels}

    def find(label_id: int) -> int:
        while parent[label_id] != label_id:
            parent[label_id] = parent[parent[label_id]]
            label_id = parent[label_id]
        return label_id

    first: dict[str, int] = {}

    def link(token: str, label_id: int) -> None:
        if token in first:
            parent[find(label_id)] = find(first[token])
        else:
            first[token] = label_id

    for label in labels:
        link("key:" + merge_key(effective[label.id]), label.id)
        if label.id in split:
            continue
        if label.official_name:
            link("key:" + merge_key(label.official_name), label.id)
        for service in ("beatport", "discogs"):
            if label.external_ids.get(service):
                link(f"{service}:{label.external_ids[service]}", label.id)

    groups: dict[int, list[int]] = defaultdict(list)
    for label in labels:
        groups[find(label.id)].append(label.id)

    merged = renamed = 0
    conn.execute("DELETE FROM label_aliases")
    for members in groups.values():
        main = max(members, key=lambda i: (by_id[i].status == _FOUND and i not in split, by_id[i].releases, -i))
        others = [i for i in members if i != main]
        if others:
            logger.info("Merging labels %s into %s (%s)",
                        [by_id[i].name for i in others], main, by_id[main].name)
        for other in others:
            _fold(conn, main, other, take_metadata=other not in split)
            merged += 1
        official = next(
            (by_id[i].official_name for i in [main, *others] if by_id[i].official_name and i not in split), None,
        )
        biggest = max(members, key=lambda i: (by_id[i].releases, -i))
        if _rename(conn, by_id[main], official or effective[biggest]):
            renamed += 1
        keys = {merge_key(by_id[i].name) for i in members} | {merge_key(effective[i]) for i in members}
        if official:
            keys.add(merge_key(official))
        conn.executemany(
            "INSERT OR IGNORE INTO label_aliases (key, label_id) VALUES (?, ?)", [(key, main) for key in keys],
        )
    return LabelMergeSummary(merged=merged, renamed=renamed)


def merge_labels_first_time(db_path: Path) -> LabelMergeSummary | None:
    """Первый запуск со склейкой: ключей ещё нет, а лейблы есть — бэкап и склейка."""
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        if conn.execute("SELECT 1 FROM label_aliases LIMIT 1").fetchone() is not None:
            return None
        if conn.execute("SELECT 1 FROM labels LIMIT 1").fetchone() is None:
            return None
        backup = _backup(db_path)
        conn.execute("BEGIN IMMEDIATE")
        try:
            summary = merge_labels(conn)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        logger.warning("Merged duplicate labels: %s merged, %s renamed (backup: %s)",
                       summary.merged, summary.renamed, backup)
        return summary
    finally:
        conn.close()


def _effective_names(labels: list[_Label]) -> dict[int, str]:
    """Название без юридической обёртки; «A / B» — тот из них, у кого больше релизов.

    Пара, ни одной части которой нет среди лейблов, остаётся целой («KR/LF», «Ki/oon»).
    """
    clean = {label.id: strip_legal(label.name) for label in labels}
    releases_by_key: dict[str, int] = defaultdict(int)
    for label in labels:
        releases_by_key[merge_key(clean[label.id])] += label.releases
    effective: dict[int, str] = {}
    for label in labels:
        name = clean[label.id]
        effective[label.id] = pick_part(label_parts(name), lambda part: releases_by_key.get(merge_key(part), 0)) or name
    return effective


def _fold(conn: sqlite3.Connection, main: int, other: int, *, take_metadata: bool = True) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO release_labels (release_id, label_id, position)
        SELECT release_id, ?, position FROM release_labels WHERE label_id = ?
        """,
        (main, other),
    )
    conn.execute(
        """
        INSERT INTO user_label_preferences (user_id, label_id, liked, liked_at, updated_at)
        SELECT user_id, ?, liked, liked_at, updated_at FROM user_label_preferences WHERE label_id = ?
        ON CONFLICT(user_id, label_id) DO UPDATE SET
            liked = MAX(user_label_preferences.liked, excluded.liked),
            liked_at = COALESCE(user_label_preferences.liked_at, excluded.liked_at)
        """,
        (main, other),
    )
    if not take_metadata:
        conn.execute("DELETE FROM labels WHERE id = ?", (other,))
        return
    # Своё у основного остаётся; чужое берётся, только если своего нет (описание, написанное
    # вручную, важнее найденного синхронизацией).
    conn.execute(
        """
        UPDATE labels SET
            image_source = CASE WHEN m.image_path IS NULL THEN o.image_source ELSE m.image_source END,
            image_path = COALESCE(m.image_path, o.image_path),
            description_source = CASE WHEN m.description IS NULL OR m.take_description
                                      THEN COALESCE(o.description_source, m.description_source)
                                      ELSE m.description_source END,
            description = CASE WHEN m.description IS NULL OR m.take_description
                               THEN COALESCE(o.description, m.description) ELSE m.description END,
            links_json = CASE WHEN COALESCE(m.links_json, '[]') IN ('[]', '') THEN o.links_json ELSE m.links_json END,
            external_ids_json = CASE WHEN COALESCE(m.external_ids_json, '{}') IN ('{}', '')
                                     THEN o.external_ids_json ELSE m.external_ids_json END,
            official_name = COALESCE(m.official_name, o.official_name),
            metadata_synced_at = COALESCE(m.metadata_synced_at, o.metadata_synced_at)
        FROM (SELECT * FROM labels WHERE id = ?) AS o,
             (SELECT *, (description_source IS NOT 'editorial'
                         AND (SELECT description_source FROM labels WHERE id = ?) = 'editorial') AS take_description
              FROM labels WHERE id = ?) AS m
        WHERE labels.id = m.id
        """,
        (other, other, main),
    )
    conn.execute("DELETE FROM labels WHERE id = ?", (other,))


def _rename(conn: sqlite3.Connection, label: _Label, name: str) -> bool:
    if not name or name == label.name:
        return False
    normalized = normalize_text(name)
    taken = conn.execute(
        "SELECT 1 FROM labels WHERE normalized_name = ? AND id != ?", (normalized, label.id),
    ).fetchone()
    if taken is not None:
        return False
    conn.execute(
        "UPDATE labels SET name = ?, normalized_name = ?, updated_at = ? WHERE id = ?",
        (name, normalized, utc_now(), label.id),
    )
    if label.status in _RETRY_WHEN_RENAMED:
        # Искали под старым названием («Universal Music Division Decca…») — поискать под новым.
        conn.execute("DELETE FROM label_sync_state WHERE label_id = ?", (label.id,))
    return True


def _ids(raw: str | None) -> dict[str, str]:
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return {str(k): str(v) for k, v in value.items() if v} if isinstance(value, dict) else {}


def _backup(db_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = db_path.with_name(f"{db_path.name}.pre-label-merge-{stamp}.bak")
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        conn.execute("VACUUM INTO ?", (str(target),))
    finally:
        conn.close()
    return target
