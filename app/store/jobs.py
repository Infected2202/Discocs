"""Store Analysis jobs domain: jobs, workers, tasks.

Part of the app/store package — Stage 7 refactoring.
Do not import this module directly; use app.store instead.
"""
from __future__ import annotations

from __future__ import annotations

import logging
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta
try:
    from datetime import UTC
except ImportError:
    from datetime import timezone as _tz; UTC = _tz.utc
from pathlib import Path
from threading import Lock
from uuid import uuid4

import numpy as np

from app.store._helpers import (
    row_to_analysis_job,
    row_to_analysis_task,
    row_to_analysis_worker,
)
from app.library import (
    ArtistCredit,
    TrackMetadataEnvelope,
    clean_display_text,
    envelope_from_scanned_track,
    envelope_from_track_row,
    normalize_text,
    parse_artist_credit,
    release_identity_key,
    release_title_for_envelope,
)
from app.models import (
    AnalysisJob,
    AnalysisTask,
    AnalysisWorker,
    Artist,
    ArtistSummaryRow,
    COMPLETION_FRACTION,
    EARLY_SKIP_FRACTION,
    EARLY_SKIP_SECONDS,
    ExternalTrack,
    FeatureFilter,
    FeatureSummary,
    FeatureTrack,
    GeneratedMix,
    GeneratedMixItem,
    GENERATED_MIX_STATUSES,
    GENERATED_MIX_TYPES,
    HeadSummary,
    InstantMixRequest,
    LATE_SKIP_FRACTION,
    MEANINGFUL_LISTEN_FRACTION,
    MEANINGFUL_LISTEN_SECONDS,
    NormalizationStatus,
    Playlist,
    PlaylistItem,
    PlaybackEvent,
    PLAYBACK_EVENT_TYPES,
    PlaybackEventResult,
    PLAYBACK_MODES,
    PLAYBACK_REPEAT_MODES,
    PlaybackSession,
    PLAYBACK_SESSION_STATUSES,
    PLAYBACK_SOURCE_TYPES,
    QueueItem,
    QUEUE_ORIGINS,
    QUEUE_STATUSES,
    Release,
    ReleaseSummaryRow,
    ReleaseTrackRow,
    SimilarTrack,
    Track,
    TrackFeature,
    TrackListing,
    TrackModelOutput,
    TrackPrediction,
    UserArtistPreference,
    UserReleasePreference,
    UserTrackPreference,
    utc_now,
)
from app.scanner import ScannedTrack


logger = logging.getLogger(__name__)
INIT_LOCK = Lock()
INITIALIZED_DB_PATHS: set[Path] = set()

class JobsStoreMixin:
    def create_analysis_job(
        self,
        model_name: str,
        limit: int | None,
        *,
        kind: str = "analyze",
        tracks: list[Track] | None = None,
        local_executor_enabled: bool = True,
        workers: int = 1,
        tf_threads: int = 1,
        max_attempts: int = 3,
        job_id: str | None = None,
    ) -> AnalysisJob:
        now = utc_now()
        job_id = job_id or str(uuid4())
        tracks = tracks if tracks is not None else self.list_tracks_missing_embedding(model_name, limit=limit)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO analysis_jobs (
                    id, kind, model_name, status, total, local_executor_enabled, workers,
                    tf_threads, max_attempts, message, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    kind,
                    model_name,
                    "running" if tracks else "completed",
                    len(tracks),
                    int(local_executor_enabled),
                    int(workers),
                    int(tf_threads),
                    int(max_attempts),
                    f"Queued {len(tracks)} tracks for {model_name}",
                    now,
                    now,
                ),
            )
            conn.executemany(
                """
                INSERT INTO analysis_tasks (
                    id, job_id, track_id, model_name, status, max_attempts, path,
                    file_size, mtime, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(uuid4()),
                        job_id,
                        track.id,
                        model_name,
                        int(max_attempts),
                        track.path,
                        track.file_size,
                        track.mtime,
                        now,
                        now,
                    )
                    for track in tracks
                ],
            )
            if not tracks:
                conn.execute(
                    "UPDATE analysis_jobs SET finished_at = ?, message = ? WHERE id = ?",
                    (now, "Analyzed 0 tracks, failed 0", job_id),
                )
        return self.get_analysis_job(job_id)  # type: ignore[return-value]

    def create_progress_job(
        self,
        kind: str,
        model_name: str,
        *,
        total: int = 0,
        message: str = "",
        job_id: str | None = None,
    ) -> AnalysisJob:
        now = utc_now()
        job_id = job_id or str(uuid4())
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO analysis_jobs (
                    id, kind, model_name, status, total, progress_done,
                    progress_failed, local_executor_enabled, workers, tf_threads,
                    max_attempts, message, created_at, updated_at
                )
                VALUES (?, ?, ?, 'running', ?, 0, 0, 0, 0, 0, 1, ?, ?, ?)
                """,
                (
                    job_id,
                    kind,
                    model_name,
                    max(int(total), 0),
                    message or f"Running {kind}",
                    now,
                    now,
                ),
            )
        return self._get_analysis_job_no_refresh(job_id)  # type: ignore[return-value]

    def update_progress_job(
        self,
        job_id: str,
        *,
        done: int | None = None,
        failed: int | None = None,
        total: int | None = None,
        status: str | None = None,
        message: str | None = None,
        finished: bool = False,
    ) -> AnalysisJob | None:
        now = utc_now()
        updates = ["updated_at = ?"]
        params: list[object] = [now]
        if done is not None:
            updates.append("progress_done = ?")
            params.append(max(int(done), 0))
        if failed is not None:
            updates.append("progress_failed = ?")
            params.append(max(int(failed), 0))
        if total is not None:
            updates.append("total = ?")
            params.append(max(int(total), 0))
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if message is not None:
            updates.append("message = ?")
            params.append(message)
        if finished:
            updates.append("finished_at = ?")
            params.append(now)
        params.append(job_id)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE analysis_jobs SET {', '.join(updates)} WHERE id = ?",
                params,
            )
        return self._get_analysis_job_no_refresh(job_id)

    def expire_analysis_leases(self, now: str | None = None) -> int:
        now = now or utc_now()
        with self.connect() as conn:
            job_rows = conn.execute(
                """
                SELECT DISTINCT job_id
                FROM analysis_tasks
                WHERE status = 'leased'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at < ?
                """,
                (now,),
            ).fetchall()
            cursor = conn.execute(
                """
                UPDATE analysis_tasks
                SET status = 'queued',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    stage = 'lease_expired',
                    updated_at = ?
                WHERE status = 'leased'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at < ?
                """,
                (now, now),
            )
            expired = int(cursor.rowcount)
        for row in job_rows:
            self.refresh_analysis_job(str(row["job_id"]))
        return expired

    def register_analysis_worker(self, worker_id: str, models: list[str]) -> None:
        now = utc_now()
        models_text = ",".join(sorted(set(models)))
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO analysis_workers (worker_id, models, status, last_seen_at, created_at)
                VALUES (?, ?, 'online', ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    models = excluded.models,
                    status = 'online',
                    last_seen_at = excluded.last_seen_at,
                    stage = COALESCE(analysis_workers.stage, 'heartbeat')
                """,
                (worker_id, models_text, now, now),
            )

    def heartbeat_analysis_worker(
        self,
        worker_id: str,
        models: list[str],
        *,
        min_interval_seconds: int = 60,
    ) -> bool:
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        models_text = ",".join(sorted(set(models)))
        with self.connect() as conn:
            row = conn.execute(
                "SELECT models, last_seen_at FROM analysis_workers WHERE worker_id = ?",
                (worker_id,),
            ).fetchone()
            if row is not None and row["models"] == models_text:
                try:
                    last_seen_at = datetime.fromisoformat(str(row["last_seen_at"]))
                except ValueError:
                    last_seen_at = None
                if last_seen_at is not None:
                    if last_seen_at.tzinfo is None:
                        last_seen_at = last_seen_at.replace(tzinfo=UTC)
                    if now_dt - last_seen_at < timedelta(seconds=max(min_interval_seconds, 0)):
                        return False
            conn.execute(
                """
                INSERT INTO analysis_workers (worker_id, models, status, last_seen_at, created_at)
                VALUES (?, ?, 'online', ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    models = excluded.models,
                    status = 'online',
                    last_seen_at = excluded.last_seen_at,
                    stage = COALESCE(analysis_workers.stage, 'heartbeat')
                """,
                (worker_id, models_text, now, now),
            )
        return True

    def update_analysis_worker(
        self,
        worker_id: str,
        *,
        status: str = "online",
        stage: str | None = None,
        message: str | None = None,
        current_task_id: str | None = None,
        claimed_delta: int = 0,
        completed_delta: int = 0,
        failed_delta: int = 0,
        released_delta: int = 0,
    ) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE analysis_workers
                SET status = ?,
                    last_seen_at = ?,
                    stage = COALESCE(?, stage),
                    message = COALESCE(?, message),
                    current_task_id = ?,
                    claimed_count = claimed_count + ?,
                    completed_count = completed_count + ?,
                    failed_count = failed_count + ?,
                    released_count = released_count + ?
                WHERE worker_id = ?
                """,
                (
                    status,
                    now,
                    stage,
                    message,
                    current_task_id,
                    int(claimed_delta),
                    int(completed_delta),
                    int(failed_delta),
                    int(released_delta),
                    worker_id,
                ),
            )

    def list_analysis_workers(self) -> list[AnalysisWorker]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM analysis_workers ORDER BY last_seen_at DESC"
            ).fetchall()
        return [row_to_analysis_worker(row) for row in rows]

    def analysis_job_task_summary(self, job_id: str) -> dict[str, object]:
        with self.connect() as conn:
            status_rows = conn.execute(
                """
                SELECT status, stage, COUNT(*) AS count
                FROM analysis_tasks
                WHERE job_id = ?
                GROUP BY status, stage
                ORDER BY status, stage
                """,
                (job_id,),
            ).fetchall()
            leased_rows = conn.execute(
                """
                SELECT lease_owner, COUNT(*) AS count
                FROM analysis_tasks
                WHERE job_id = ?
                  AND status = 'leased'
                  AND lease_owner IS NOT NULL
                GROUP BY lease_owner
                ORDER BY count DESC, lease_owner
                """,
                (job_id,),
            ).fetchall()
            oldest_lease = conn.execute(
                """
                SELECT lease_owner, lease_expires_at, stage, updated_at, path
                FROM analysis_tasks
                WHERE job_id = ?
                  AND status = 'leased'
                ORDER BY lease_expires_at, updated_at
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            error_rows = conn.execute(
                """
                SELECT id, track_id, status, error, error_type, stage, updated_at, lease_owner, path
                FROM analysis_tasks
                WHERE job_id = ?
                  AND error IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT 5
                """,
                (job_id,),
            ).fetchall()
        return {
            "status_breakdown": [
                {
                    "status": str(row["status"]),
                    "stage": row["stage"],
                    "count": int(row["count"] or 0),
                }
                for row in status_rows
            ],
            "leased_workers": [
                {
                    "worker_id": str(row["lease_owner"]),
                    "count": int(row["count"] or 0),
                }
                for row in leased_rows
            ],
            "oldest_lease": (
                {
                    "worker_id": oldest_lease["lease_owner"],
                    "lease_expires_at": oldest_lease["lease_expires_at"],
                    "stage": oldest_lease["stage"],
                    "updated_at": oldest_lease["updated_at"],
                    "path": oldest_lease["path"],
                }
                if oldest_lease is not None
                else None
            ),
            "recent_errors": [
                {
                    "task_id": str(row["id"]),
                    "track_id": int(row["track_id"]),
                    "status": str(row["status"]),
                    "error": str(row["error"]),
                    "error_type": row["error_type"],
                    "stage": row["stage"],
                    "updated_at": row["updated_at"],
                    "worker_id": row["lease_owner"],
                    "path": row["path"],
                }
                for row in error_rows
            ],
        }

    def list_analysis_job_tasks(
        self,
        job_id: str,
        *,
        statuses: list[str] | None = None,
        limit: int = 100,
    ) -> list[AnalysisTask]:
        params: list[object] = [job_id]
        status_clause = ""
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            status_clause = f" AND status IN ({placeholders})"
            params.extend(statuses)
        params.append(int(limit))
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM analysis_tasks
                WHERE job_id = ?{status_clause}
                ORDER BY
                    CASE status
                        WHEN 'leased' THEN 0
                        WHEN 'queued' THEN 1
                        WHEN 'failed_retryable' THEN 2
                        WHEN 'final_failed' THEN 3
                        ELSE 4
                    END,
                    updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [row_to_analysis_task(row) for row in rows]

    def claim_analysis_tasks(
        self,
        worker_id: str,
        models: list[str],
        *,
        limit: int,
        lease_seconds: int = 300,
    ) -> list[AnalysisTask]:
        if not models or limit <= 0:
            return []
        self.expire_analysis_leases()
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        lease_expires_at = (now_dt + timedelta(seconds=lease_seconds)).isoformat()
        placeholders = ",".join("?" for _ in models)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                f"""
                SELECT t.* FROM analysis_tasks t
                JOIN analysis_jobs j ON j.id = t.job_id
                WHERE t.status = 'queued'
                  AND j.status = 'running'
                  AND t.model_name IN ({placeholders})
                ORDER BY t.created_at, t.track_id
                LIMIT ?
                """,
                [*models, int(limit)],
            ).fetchall()
            task_ids = [str(row["id"]) for row in rows]
            if task_ids:
                id_placeholders = ",".join("?" for _ in task_ids)
                conn.execute(
                    f"""
                    UPDATE analysis_tasks
                    SET status = 'leased',
                        attempts = attempts + 1,
                        lease_owner = ?,
                        lease_expires_at = ?,
                        error = NULL,
                        error_type = NULL,
                        stage = 'claimed',
                        updated_at = ?
                    WHERE id IN ({id_placeholders})
                    """,
                    [worker_id, lease_expires_at, now, *task_ids],
                )
        if task_ids:
            self.update_analysis_worker(
                worker_id,
                stage="claimed",
                message=f"claimed {len(task_ids)} task(s)",
                claimed_delta=len(task_ids),
            )
        return [
            row_to_analysis_task(
                {
                    **dict(row),
                    "status": "leased",
                    "attempts": int(row["attempts"]) + 1,
                    "lease_owner": worker_id,
                    "lease_expires_at": lease_expires_at,
                }
            )
            for row in rows
        ]

    def get_analysis_task(self, task_id: str) -> AnalysisTask | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM analysis_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        return row_to_analysis_task(row) if row else None

    def latest_track_analysis_states(
        self,
        track_ids: list[int],
        model_name: str,
    ) -> dict[int, dict[str, object]]:
        ids = list(dict.fromkeys(int(track_id) for track_id in track_ids))
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT task.* FROM analysis_tasks task
                WHERE task.track_id IN ({placeholders})
                  AND task.model_name=?
                  AND task.updated_at=(
                      SELECT MAX(latest.updated_at) FROM analysis_tasks latest
                      WHERE latest.track_id=task.track_id
                        AND latest.model_name=task.model_name
                  )
                """,
                (*ids, model_name),
            ).fetchall()
        return {int(row["track_id"]): dict(row) for row in rows}

    def complete_analysis_task(
        self,
        task_id: str,
        worker_id: str | None = None,
        *,
        refresh_job: bool = True,
        update_worker: bool = True,
    ) -> None:
        now = utc_now()
        params: list[object] = [now, now, task_id]
        owner_clause = ""
        if worker_id is not None:
            owner_clause = " AND lease_owner = ?"
            params.append(worker_id)
        with self.connect() as conn:
            conn.execute(
                f"""
                UPDATE analysis_tasks
                SET status = 'completed',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    error = NULL,
                    error_type = NULL,
                    stage = 'completed',
                    updated_at = ?,
                    completed_at = ?
                WHERE id = ?{owner_clause}
                """,
                params,
            )
        if update_worker and worker_id is not None:
            self.update_analysis_worker(
                worker_id,
                stage="completed",
                completed_delta=1,
                current_task_id=None,
            )
        if not refresh_job:
            return
        task = self.get_analysis_task(task_id)
        if task is not None:
            self.refresh_analysis_job(task.job_id)

    def fail_analysis_task(
        self,
        task_id: str,
        *,
        error: str,
        error_type: str,
        stage: str,
        worker_id: str | None = None,
        retryable: bool = True,
    ) -> None:
        now = utc_now()
        task = self.get_analysis_task(task_id)
        if task is None:
            return
        if task.status == "final_failed" and task.stage == "cancelled":
            return
        status = "queued" if retryable and task.attempts < task.max_attempts else "final_failed"
        params: list[object] = [
            status,
            error,
            error_type,
            stage,
            now,
            task_id,
        ]
        owner_clause = ""
        if worker_id is not None:
            owner_clause = " AND lease_owner = ?"
            params.append(worker_id)
        with self.connect() as conn:
            conn.execute(
                f"""
                UPDATE analysis_tasks
                SET status = ?,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    error = ?,
                    error_type = ?,
                    stage = ?,
                    updated_at = ?
                WHERE id = ?{owner_clause}
                """,
                params,
            )
        if worker_id is not None:
            self.update_analysis_worker(
                worker_id,
                stage=stage,
                message=error[:240],
                failed_delta=1 if status == "final_failed" else 0,
                current_task_id=None,
            )
        self.refresh_analysis_job(task.job_id)

    def release_analysis_tasks(self, worker_id: str, task_ids: list[str] | None = None) -> int:
        now = utc_now()
        params: list[object] = [now, worker_id]
        task_clause = ""
        if task_ids:
            placeholders = ",".join("?" for _ in task_ids)
            task_clause = f" AND id IN ({placeholders})"
            params.extend(task_ids)
        with self.connect() as conn:
            job_rows = conn.execute(
                f"""
                SELECT DISTINCT job_id
                FROM analysis_tasks
                WHERE status = 'leased'
                  AND lease_owner = ?{task_clause}
                """,
                [worker_id, *(task_ids or [])],
            ).fetchall()
            cursor = conn.execute(
                f"""
                UPDATE analysis_tasks
                SET status = 'queued',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    stage = 'released',
                    updated_at = ?
                WHERE status = 'leased'
                  AND lease_owner = ?{task_clause}
                """,
                params,
            )
            released = int(cursor.rowcount)
        if released:
            self.update_analysis_worker(
                worker_id,
                stage="released",
                message=f"released {released} task(s)",
                released_delta=released,
                current_task_id=None,
            )
        for row in job_rows:
            self.refresh_analysis_job(str(row["job_id"]))
        return released

    def cancel_analysis_job(self, job_id: str, reason: str = "Cancelled by user") -> AnalysisJob | None:
        now = utc_now()
        with self.connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM analysis_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if exists is None:
                return None
            conn.execute(
                """
                UPDATE analysis_tasks
                SET status = 'final_failed',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    error = ?,
                    error_type = 'Cancelled',
                    stage = 'cancelled',
                    updated_at = ?
                WHERE job_id = ?
                  AND status IN ('queued', 'leased', 'failed_retryable')
                """,
                (reason, now, job_id),
            )
            counts = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS done,
                    SUM(CASE WHEN status = 'final_failed' THEN 1 ELSE 0 END) AS failed
                FROM analysis_tasks
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
            total = int(counts["total"] or 0)
            done = int(counts["done"] or 0)
            failed = int(counts["failed"] or 0)
            conn.execute(
                """
                UPDATE analysis_jobs
                SET status = 'cancelled',
                    total = ?,
                    message = ?,
                    updated_at = ?,
                    finished_at = ?
                WHERE id = ?
                """,
                (total, f"Cancelled: done {done}/{total}, failed {failed}", now, now, job_id),
            )
        return self._get_analysis_job_no_refresh(job_id)

    def refresh_analysis_job(self, job_id: str) -> AnalysisJob | None:
        now = utc_now()
        with self.connect() as conn:
            current = conn.execute(
                "SELECT kind, status, updated_at, finished_at FROM analysis_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if current is None:
                return None
            if not str(current["kind"]).startswith("analyze"):
                return self._get_analysis_job_no_refresh(job_id)
            if str(current["status"]) not in {"queued", "running"}:
                if current["finished_at"] is None:
                    conn.execute(
                        "UPDATE analysis_jobs SET finished_at = ? WHERE id = ?",
                        (current["updated_at"] or now, job_id),
                    )
                return self._get_analysis_job_no_refresh(job_id)
            counts = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS done,
                    SUM(CASE WHEN status = 'final_failed' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued,
                    SUM(CASE WHEN status = 'leased' THEN 1 ELSE 0 END) AS leased
                FROM analysis_tasks
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
            if counts is None:
                return None
            total = int(counts["total"] or 0)
            done = int(counts["done"] or 0)
            failed = int(counts["failed"] or 0)
            completed = done + failed >= total
            status = "completed" if completed else "running"
            message = f"Analyzed {done}/{total} tracks, failed {failed}"
            finished_at = now if completed else None
            conn.execute(
                """
                UPDATE analysis_jobs
                SET status = ?, total = ?, message = ?, updated_at = ?,
                    finished_at = COALESCE(finished_at, ?)
                WHERE id = ?
                """,
                (status, total, message, now, finished_at, job_id),
            )
        return self._get_analysis_job_no_refresh(job_id)

    def get_analysis_job(self, job_id: str) -> AnalysisJob | None:
        self.expire_analysis_leases()
        self.refresh_analysis_job_counts_only(job_id)
        return self._get_analysis_job_no_refresh(job_id)

    def get_analysis_job_status(self, job_id: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT status FROM analysis_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return str(row["status"]) if row else None

    def refresh_active_analysis_jobs(self) -> None:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id
                FROM analysis_jobs
                WHERE status IN ('queued', 'running')
                """
            ).fetchall()
        for row in rows:
            self.refresh_analysis_job(str(row["id"]))

    def has_active_analysis_job(self) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM analysis_jobs
                WHERE status IN ('queued', 'running')
                LIMIT 1
                """
            ).fetchone()
        return row is not None

    def _get_analysis_job_no_refresh(self, job_id: str) -> AnalysisJob | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT j.*,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'completed' THEN 1 ELSE 0 END)
                        ELSE j.progress_done
                    END AS done,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'final_failed' THEN 1 ELSE 0 END)
                        ELSE j.progress_failed
                    END AS failed,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'queued' THEN 1 ELSE 0 END)
                        ELSE 0
                    END AS queued,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'leased' THEN 1 ELSE 0 END)
                        ELSE 0
                    END AS leased,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'final_failed' THEN 1 ELSE 0 END)
                        ELSE j.progress_failed
                    END AS final_failed
                FROM analysis_jobs j
                LEFT JOIN analysis_tasks t ON t.job_id = j.id
                WHERE j.id = ?
                GROUP BY j.id
                """,
                (job_id,),
            ).fetchone()
        return row_to_analysis_job(row) if row else None

    def refresh_analysis_job_counts_only(self, job_id: str) -> None:
        with self.connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM analysis_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        if exists is not None:
            self.refresh_analysis_job(job_id)

    def recent_analysis_jobs(
        self,
        limit: int = 20,
        statuses: list[str] | None = None,
    ) -> list[AnalysisJob]:
        status_filter = ""
        params: list[object] = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            status_filter = f"WHERE status IN ({placeholders})"
            params.extend(statuses)
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT j.*,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'completed' THEN 1 ELSE 0 END)
                        ELSE j.progress_done
                    END AS done,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'final_failed' THEN 1 ELSE 0 END)
                        ELSE j.progress_failed
                    END AS failed,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'queued' THEN 1 ELSE 0 END)
                        ELSE 0
                    END AS queued,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'leased' THEN 1 ELSE 0 END)
                        ELSE 0
                    END AS leased,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'final_failed' THEN 1 ELSE 0 END)
                        ELSE j.progress_failed
                    END AS final_failed
                FROM (
                    SELECT *
                    FROM analysis_jobs
                    {status_filter}
                    ORDER BY created_at DESC
                    LIMIT ?
                ) j
                LEFT JOIN analysis_tasks t ON t.job_id = j.id
                GROUP BY j.id
                ORDER BY j.created_at DESC
                """,
                params,
            ).fetchall()
        return [row_to_analysis_job(row) for row in rows]

    def count_recent_finished_analysis_tasks(self, job_id: str, since: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM analysis_tasks
                WHERE job_id = ?
                  AND status IN ('completed', 'final_failed')
                  AND updated_at >= ?
                """,
                (job_id, since),
            ).fetchone()
        return int(row[0]) if row else 0

