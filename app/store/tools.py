"""Store Tools domain: инструменты рабочей машины (docs/tools.md).

Воркер на ПК (tools/agent) сам ходит в discocs: сообщает состояние, берёт задачи из админки, присылает
прогресс и результаты. Здесь — очередь задач, состояние воркеров и итоги агента описаний лейблов.
Do not import this module directly; use app.store instead.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from app.models import utc_now
from app.store.label_sync import LABEL_SYNC_SELF_RELEASED
from app.store.labels import AGENT_SOURCE, EDITORIAL_SOURCE, clean_description

# Описания, которые агент заменяет своими: найденные синхронизацией лейблов.
REPLACEABLE_SOURCES = ("wikipedia_ru", "wikipedia_en", "discogs", "beatport")

TOOL_JOB_ACTIVE = ("queued", "running", "paused")
TOOL_JOB_FINISHED = ("done", "failed", "cancelled")

AGENT_WRITTEN = "written"
AGENT_NOT_FOUND = "not_found"
AGENT_FAILED = "failed"
# Агент написал текст, но у лейбла редакционное описание — его агент не трогает.
AGENT_SKIPPED = "skipped"
AGENT_REVERTED = "reverted"

# Какие лейблы берёт запуск describe.
DESCRIBE_SCOPES = ("missing", "replace", "not_found", "ids")

_LABEL_RELEASES = "(SELECT COUNT(DISTINCT rl.release_id) FROM release_labels rl WHERE rl.label_id = l.id)"


def _loads(value: str | None, default: object) -> object:
    if not value:
        return default
    try:
        return json.loads(value)
    except ValueError:
        return default


def _job_dict(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "tool": row["tool"],
        "action": row["action"],
        "params": _loads(row["params_json"], {}),
        "status": row["status"],
        "control": row["control"],
        "progress": _loads(row["progress_json"], {}),
        "message": row["message"],
        "worker_id": row["worker_id"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


class ToolsStoreMixin:
    # --- воркеры -----------------------------------------------------------

    def tool_worker_seen(self, worker_id: str, state: dict[str, object]) -> None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                """
                INSERT INTO tool_workers (id, state_json, seen_at) VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET state_json = excluded.state_json, seen_at = excluded.seen_at
                """,
                (worker_id, json.dumps(state, ensure_ascii=False), utc_now()),
            )

    def tool_workers(self) -> list[dict[str, object]]:
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute("SELECT id, state_json, seen_at FROM tool_workers ORDER BY seen_at DESC").fetchall()
        return [{"id": row["id"], "state": _loads(row["state_json"], {}), "seen_at": row["seen_at"]} for row in rows]

    # --- задачи ------------------------------------------------------------

    def create_tool_job(self, tool: str, action: str, params: dict[str, object]) -> int:
        with self.connect() as conn:  # type: ignore[attr-defined]
            cursor = conn.execute(
                """
                INSERT INTO tool_jobs (tool, action, params_json, status, control, created_at)
                VALUES (?, ?, ?, 'queued', 'run', ?)
                """,
                (tool, action, json.dumps(params, ensure_ascii=False), utc_now()),
            )
            return int(cursor.lastrowid)

    def tool_job(self, job_id: int) -> dict[str, object] | None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            row = conn.execute("SELECT * FROM tool_jobs WHERE id = ?", (job_id,)).fetchone()
        return _job_dict(row) if row else None

    def tool_jobs(self, *, limit: int = 20) -> list[dict[str, object]]:
        """Последние задачи; в params списки лейблов не отдаются — их сотни."""
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute("SELECT * FROM tool_jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        jobs = [_job_dict(row) for row in rows]
        for job in jobs:
            params = dict(job["params"]) if isinstance(job["params"], dict) else {}
            params.pop("label_ids", None)
            job["params"] = params
        return jobs

    def active_tool_job(self, tool: str) -> dict[str, object] | None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            row = conn.execute(
                f"SELECT * FROM tool_jobs WHERE tool = ? AND status IN {TOOL_JOB_ACTIVE} ORDER BY id LIMIT 1",
                (tool,),
            ).fetchone()
        return _job_dict(row) if row else None

    def claim_tool_job(self, worker_id: str, tools: Iterable[str]) -> dict[str, object] | None:
        """Задача для воркера: его же незаконченная (воркер перезапустился) или самая старая в очереди."""
        tools = tuple(tools)
        if not tools:
            return None
        marks = ",".join("?" for _ in tools)
        now = utc_now()
        with self.connect() as conn:  # type: ignore[attr-defined]
            # Свою задачу, отменённую, пока воркер был выключен, он уже не доделает — закрыть.
            conn.execute(
                """
                UPDATE tool_jobs SET status = 'cancelled', finished_at = ?
                WHERE worker_id = ? AND control = 'cancel' AND status IN ('running', 'paused')
                """,
                (now, worker_id),
            )
            row = conn.execute(
                f"""
                SELECT * FROM tool_jobs
                WHERE tool IN ({marks}) AND control != 'cancel'
                  AND (status = 'queued' OR (status IN ('running', 'paused') AND worker_id = ?))
                ORDER BY status = 'queued', id LIMIT 1
                """,
                (*tools, worker_id),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE tool_jobs SET status = CASE WHEN status = 'queued' THEN 'running' ELSE status END,
                    worker_id = ?, started_at = COALESCE(started_at, ?)
                WHERE id = ?
                """,
                (worker_id, now, row["id"]),
            )
            row = conn.execute("SELECT * FROM tool_jobs WHERE id = ?", (row["id"],)).fetchone()
        return _job_dict(row)

    def update_tool_job(
        self,
        job_id: int,
        *,
        status: str | None = None,
        progress: dict[str, object] | None = None,
        message: str | None = None,
    ) -> str | None:
        """Прогресс от воркера; ответ — чего хочет админка (run/pause/cancel), None — задачи нет."""
        with self.connect() as conn:  # type: ignore[attr-defined]
            row = conn.execute("SELECT control, status FROM tool_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            if row["status"] in TOOL_JOB_FINISHED:
                return "cancel" if row["status"] == "cancelled" else row["control"]
            conn.execute(
                """
                UPDATE tool_jobs SET status = COALESCE(?, status), progress_json = COALESCE(?, progress_json),
                    message = COALESCE(?, message),
                    finished_at = CASE WHEN ? IN ('done', 'failed', 'cancelled') THEN ? ELSE finished_at END
                WHERE id = ?
                """,
                (
                    status,
                    json.dumps(progress, ensure_ascii=False) if progress is not None else None,
                    message,
                    status,
                    utc_now(),
                    job_id,
                ),
            )
            return str(row["control"])

    def set_tool_job_control(self, job_id: int, control: str, *, worker_online: bool = True) -> dict[str, object] | None:
        """Пауза/продолжение/отмена из админки. Отмена закрывает сразу задачу, которую ещё никто не взял или
        чей воркер не на связи: подтвердить её некому, а висящая задача не даёт поставить новую."""
        with self.connect() as conn:  # type: ignore[attr-defined]
            row = conn.execute("SELECT status FROM tool_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            if row["status"] not in TOOL_JOB_FINISHED:
                conn.execute("UPDATE tool_jobs SET control = ? WHERE id = ?", (control, job_id))
                if control == "cancel" and (row["status"] == "queued" or not worker_online):
                    conn.execute(
                        "UPDATE tool_jobs SET status = 'cancelled', finished_at = ? WHERE id = ?",
                        (utc_now(), job_id),
                    )
        return self.tool_job(job_id)

    # --- describe: какие лейблы и что о них знает библиотека ----------------

    def describe_label_ids(
        self, scope: str, *, min_releases: int = 1, label_ids: Iterable[int] = ()
    ) -> list[int]:
        """Лейблы для запуска describe — крупные первыми.

        missing — без описания; replace — без описания или с найденным синхронизацией (Википедия, Discogs,
        Beatport); not_found — где агент уже ничего не нашёл; ids — выбранные. Редакционное описание и
        самовыпуски («Independent», «Not On Label») не берутся никогда; в missing/replace не берутся и
        лейблы, о которых агент уже ничего не нашёл, — их перебирает отдельный запуск not_found.
        """
        where = [
            f"COALESCE(l.description_source, '') != '{EDITORIAL_SOURCE}'",
            f"""NOT EXISTS (SELECT 1 FROM label_sync_state s WHERE s.label_id = l.id
                            AND s.status = '{LABEL_SYNC_SELF_RELEASED}')""",
            f"{_LABEL_RELEASES} >= ?",
        ]
        args: list[object] = [max(1, min_releases)]
        not_tried = f"""NOT EXISTS (SELECT 1 FROM label_agent_descriptions a WHERE a.label_id = l.id
                                    AND a.status = '{AGENT_NOT_FOUND}')"""
        if scope == "missing":
            where += ["l.description IS NULL", not_tried]
        elif scope == "replace":
            marks = ",".join("?" for _ in REPLACEABLE_SOURCES)
            where += [f"(l.description IS NULL OR l.description_source IN ({marks}))", not_tried]
            args += list(REPLACEABLE_SOURCES)
        elif scope == "not_found":
            where.append(f"""EXISTS (SELECT 1 FROM label_agent_descriptions a WHERE a.label_id = l.id
                                     AND a.status = '{AGENT_NOT_FOUND}')""")
        elif scope == "ids":
            ids = [int(i) for i in label_ids]
            if not ids:
                return []
            where.append(f"l.id IN ({','.join('?' for _ in ids)})")
            args += ids
        else:
            raise ValueError(f"unknown scope {scope!r}")
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                f"SELECT l.id FROM labels l WHERE {' AND '.join(where)} ORDER BY {_LABEL_RELEASES} DESC, l.id",
                args,
            ).fetchall()
        return [int(row["id"]) for row in rows]

    def describe_inputs(self, label_ids: Iterable[int]) -> list[dict[str, object]]:
        """Что агенту нужно о лейбле: название, внешние id, артисты и релизы из библиотеки — по ним он
        узнаёт «тот ли это лейбл» на найденных страницах."""
        out = []
        with self.connect() as conn:  # type: ignore[attr-defined]
            for label_id in label_ids:
                label = conn.execute(
                    "SELECT id, name, external_ids_json FROM labels WHERE id = ?", (label_id,)
                ).fetchone()
                if label is None:
                    continue
                artists = [row[0] for row in conn.execute(
                    """
                    SELECT a.name FROM release_labels rl JOIN release_artists ra ON ra.release_id = rl.release_id
                    JOIN artists a ON a.id = ra.artist_id
                    WHERE rl.label_id = ? AND a.normalized_name != 'various artists'
                    GROUP BY a.id ORDER BY COUNT(*) DESC, a.name LIMIT 12
                    """,
                    (label_id,),
                )]
                releases = [row[0] for row in conn.execute(
                    """
                    SELECT r.title || COALESCE(' (' || r.release_year || ')', '') FROM release_labels rl
                    JOIN releases r ON r.id = rl.release_id WHERE rl.label_id = ?
                    ORDER BY r.release_year DESC, r.title LIMIT 30
                    """,
                    (label_id,),
                )]
                out.append({
                    "id": int(label["id"]),
                    "name": label["name"],
                    "external_ids": _loads(label["external_ids_json"], {}),
                    "artists": artists,
                    "releases": releases,
                })
        return out

    def describe_next(self, job_id: int, *, limit: int, exclude: Iterable[int] = ()) -> list[dict[str, object]]:
        """Следующие лейблы задачи, по которым ещё нет итога этой задачи (exclude — те, что воркер уже
        разбирает). Воркер перезапустился — недоделанные вернутся снова."""
        job = self.tool_job(job_id)
        if job is None:
            return []
        params = job["params"] if isinstance(job["params"], dict) else {}
        skip = {int(i) for i in exclude}
        with self.connect() as conn:  # type: ignore[attr-defined]
            done = {int(row[0]) for row in conn.execute(
                "SELECT label_id FROM label_agent_descriptions WHERE job_id = ?", (job_id,)
            )}
        todo = [int(i) for i in params.get("label_ids", []) if int(i) not in done and int(i) not in skip]
        return self.describe_inputs(todo[: max(0, limit)])

    def describe_job_counts(self, job_id: int) -> dict[str, int]:
        """Сколько лейблов задачи уже с итогом — по итогам на сервере, а не по счётчикам воркера: они
        обнуляются, когда воркер перезапускается."""
        job = self.tool_job(job_id)
        params = job["params"] if job and isinstance(job["params"], dict) else {}
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                "SELECT status, COUNT(*) FROM label_agent_descriptions WHERE job_id = ? GROUP BY status", (job_id,)
            ).fetchall()
        counts = {status: 0 for status in (AGENT_WRITTEN, AGENT_NOT_FOUND, AGENT_FAILED, AGENT_SKIPPED)}
        counts.update({row[0]: int(row[1]) for row in rows if row[0] in counts})
        return {"total": len(params.get("label_ids", [])), "done": sum(counts.values()), **counts}

    # --- describe: итоги агента ----------------------------------------------

    def save_agent_description(
        self,
        label_id: int,
        *,
        job_id: int | None,
        status: str,
        description: str | None = None,
        sources: list[dict[str, object]] | None = None,
        model: str | None = None,
        note: str | None = None,
    ) -> str | None:
        """Итог агента по лейблу; ответ — что сделано (written/skipped/not_found/failed), None — нет лейбла.

        Написанный текст сразу становится описанием лейбла (источник agent), кроме редакционного — его агент
        не трогает. Прежнее описание запоминается для отката. «Ничего не нашлось» прежнее описание не стирает.
        """
        text = clean_description(description)
        now = utc_now()
        with self.connect() as conn:  # type: ignore[attr-defined]
            label = conn.execute(
                "SELECT description, description_source FROM labels WHERE id = ?", (label_id,)
            ).fetchone()
            if label is None:
                return None
            existing = conn.execute(
                "SELECT status, previous_description, previous_source FROM label_agent_descriptions WHERE label_id = ?",
                (label_id,),
            ).fetchone()
            if status == AGENT_WRITTEN and text:
                if label["description_source"] == EDITORIAL_SOURCE:
                    outcome = AGENT_SKIPPED
                else:
                    outcome = AGENT_WRITTEN
                    conn.execute(
                        "UPDATE labels SET description = ?, description_source = ?, updated_at = ? WHERE id = ?",
                        (text, AGENT_SOURCE, now, label_id),
                    )
                # Текст агента поверх текста агента — откатывать надо к тому, что было до агента.
                if label["description_source"] == AGENT_SOURCE and existing is not None:
                    previous = (existing["previous_description"], existing["previous_source"])
                else:
                    previous = (label["description"], label["description_source"])
                record = (outcome, text, sources)
            else:
                outcome = AGENT_FAILED if status == AGENT_FAILED else AGENT_NOT_FOUND
                if existing is not None and existing["status"] == AGENT_WRITTEN \
                        and label["description_source"] == AGENT_SOURCE:
                    # Перегенерация ничего не нашла — написанное раньше остаётся.
                    conn.execute(
                        "UPDATE label_agent_descriptions SET note = ?, job_id = ?, updated_at = ? WHERE label_id = ?",
                        (note, job_id, now, label_id),
                    )
                    return outcome
                previous = (existing["previous_description"], existing["previous_source"]) if existing else (None, None)
                record = (outcome, None, None)
            conn.execute(
                """
                INSERT INTO label_agent_descriptions
                    (label_id, status, description, sources_json, model, note, job_id,
                     previous_description, previous_source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(label_id) DO UPDATE SET status = excluded.status, description = excluded.description,
                    sources_json = excluded.sources_json, model = excluded.model, note = excluded.note,
                    job_id = excluded.job_id, previous_description = excluded.previous_description,
                    previous_source = excluded.previous_source, updated_at = excluded.updated_at
                """,
                (
                    label_id,
                    record[0],
                    record[1],
                    json.dumps(record[2], ensure_ascii=False) if record[2] is not None else None,
                    model,
                    note,
                    job_id,
                    previous[0],
                    previous[1],
                    now,
                ),
            )
        return outcome

    def revert_agent_description(self, label_id: int) -> bool:
        """Вернуть описание, бывшее до агента. False — у лейбла сейчас не текст агента."""
        now = utc_now()
        with self.connect() as conn:  # type: ignore[attr-defined]
            row = conn.execute(
                """
                SELECT l.description_source, a.previous_description, a.previous_source
                FROM labels l LEFT JOIN label_agent_descriptions a ON a.label_id = l.id WHERE l.id = ?
                """,
                (label_id,),
            ).fetchone()
            if row is None or row["description_source"] != AGENT_SOURCE:
                return False
            previous = clean_description(row["previous_description"])
            conn.execute(
                "UPDATE labels SET description = ?, description_source = ?, updated_at = ? WHERE id = ?",
                (previous, row["previous_source"] if previous else None, now, label_id),
            )
            conn.execute(
                "UPDATE label_agent_descriptions SET status = ?, updated_at = ? WHERE label_id = ?",
                (AGENT_REVERTED, now, label_id),
            )
        return True

    def agent_descriptions(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, object]]:
        """Последние тексты агента, которые сейчас стоят описанием лейбла, — для выборочного просмотра."""
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                f"""
                SELECT a.label_id, l.name, a.description, a.sources_json, a.model, a.note, a.previous_source,
                       a.updated_at
                FROM label_agent_descriptions a JOIN labels l ON l.id = a.label_id
                WHERE a.status = '{AGENT_WRITTEN}' AND l.description_source = '{AGENT_SOURCE}'
                ORDER BY a.updated_at DESC, a.label_id DESC LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return [
            {
                "label_id": int(row["label_id"]),
                "name": row["name"],
                "description": row["description"],
                "sources": _loads(row["sources_json"], []),
                "model": row["model"],
                "note": row["note"],
                "previous_source": row["previous_source"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def describe_stats(self) -> dict[str, object]:
        """Покрытие описаниями лейблов, у которых есть релизы в библиотеке."""
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                f"""
                SELECT l.description_source AS source, l.description IS NULL AS missing,
                       {_LABEL_RELEASES} AS releases,
                       (SELECT s.status FROM label_sync_state s WHERE s.label_id = l.id) AS sync_status
                FROM labels l WHERE {_LABEL_RELEASES} > 0
                """
            ).fetchall()
            agent = {row[0]: int(row[1]) for row in conn.execute(
                """
                SELECT a.status, COUNT(*) FROM label_agent_descriptions a
                WHERE EXISTS (SELECT 1 FROM release_labels rl WHERE rl.label_id = a.label_id) GROUP BY a.status
                """
            )}
        by_source: dict[str, int] = {}
        missing = {"1": 0, "2": 0, "3+": 0}
        self_released = 0
        for row in rows:
            if row["sync_status"] == LABEL_SYNC_SELF_RELEASED:
                self_released += 1
                continue
            if row["missing"]:
                bucket = "3+" if row["releases"] >= 3 else str(row["releases"])
                missing[bucket] += 1
            else:
                source = row["source"] or "unknown"
                by_source[source] = by_source.get(source, 0) + 1
        return {
            "labels": len(rows) - self_released,
            "self_released": self_released,
            "with_description": sum(by_source.values()),
            "by_source": by_source,
            "missing": missing,
            "replaceable": sum(by_source.get(source, 0) for source in REPLACEABLE_SOURCES),
            "agent": {status: int(agent.get(status, 0)) for status in
                      (AGENT_WRITTEN, AGENT_NOT_FOUND, AGENT_FAILED, AGENT_SKIPPED, AGENT_REVERTED)},
        }
