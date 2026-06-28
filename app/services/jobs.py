"""Job management service.

JobStatus dataclass + create/update/finish/defer helpers.
Extracted from app/main.py.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from threading import Thread
from time import perf_counter
from typing import Callable
from uuid import uuid4

from app.state import (
    AUTO_INDEX_ANALYSIS_JOBS,
    AUTO_INDEX_LOCK,
    DEFERRED_JOB_ORDER,
    DEFERRED_JOB_STARTERS,
    DEFERRED_JOBS_LOCK,
    JOBS,
    JOBS_LOCK,
)
from app.store import Store

logger = logging.getLogger(__name__)


@dataclass
class JobStatus:
    id: str
    kind: str
    status: str
    message: str
    total: int | None = None
    done: int = 0
    failed: int = 0
    current: str | None = None
    elapsed_seconds: float = 0.0
    tracks_per_min: float | None = None
    eta_seconds: float | None = None
    error_detail: str | None = None
    started_at: float = 0.0
    created_at_epoch: float = 0.0
    finished_at: float | None = None


def create_job(kind: str, message: str) -> str:
    job_id = str(uuid4())
    now_epoch = time.time()
    with JOBS_LOCK:
        JOBS[job_id] = JobStatus(
            id=job_id,
            kind=kind,
            status="queued",
            message=message,
            started_at=perf_counter(),
            created_at_epoch=now_epoch,
        )
    logger.info("Created job job_id=%s kind=%s message=%s", job_id, kind, message)
    return job_id


def update_job(job_id: str, **changes: object) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        for key, value in changes.items():
            setattr(job, key, value)


def finish_job(job_id: str, status: str, message: str, error_detail: str | None = None) -> None:
    finished_at = perf_counter()
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        elapsed_seconds = max(0.0, finished_at - job.started_at) if job else 0.0
    update_job(
        job_id,
        status=status,
        message=message,
        current=None,
        elapsed_seconds=elapsed_seconds,
        eta_seconds=None,
        error_detail=error_detail,
        finished_at=finished_at,
    )
    log = logger.error if status == "failed" else logger.info
    log("Finished job job_id=%s status=%s message=%s", job_id, status, message)
    maybe_start_next_deferred_job()


def has_active_job(store: Store | None = None) -> bool:
    with JOBS_LOCK:
        if any(job.status in {"queued", "running"} for job in JOBS.values()):
            return True
    if store is None:
        try:
            from app.config import Settings
            from app.store import Store as _Store
            settings = Settings.from_env()
            store = _Store(settings.db_path)
            store.init()
        except Exception:
            logger.exception("Failed to inspect durable jobs while checking active queue state")
            return True
    return store.has_active_analysis_job()


def create_deferred_job_if_busy(
    kind: str,
    message: str,
    starter_factory: Callable[[str], Callable[[], None]],
    *,
    store: Store | None = None,
) -> tuple[str | None, bool]:
    with DEFERRED_JOBS_LOCK:
        if not has_active_job(store):
            return None, False
        job_id = create_job(kind, message)
        DEFERRED_JOB_STARTERS[job_id] = starter_factory(job_id)
        DEFERRED_JOB_ORDER.append(job_id)
    update_job(
        job_id,
        status="deferred",
        message=f"Waiting for previous job: {message}",
        current=None,
    )
    logger.info("Deferred job job_id=%s kind=%s message=%s", job_id, kind, message)
    return job_id, True


def _run_deferred_job(job_id: str) -> None:
    with DEFERRED_JOBS_LOCK:
        starter = DEFERRED_JOB_STARTERS.get(job_id)
    if starter is None:
        return
    try:
        starter()
    finally:
        with DEFERRED_JOBS_LOCK:
            DEFERRED_JOB_STARTERS.pop(job_id, None)
            if job_id in DEFERRED_JOB_ORDER:
                DEFERRED_JOB_ORDER.remove(job_id)
        maybe_start_next_deferred_job()


def maybe_start_next_deferred_job() -> str | None:
    with DEFERRED_JOBS_LOCK:
        if has_active_job():
            return None
        next_job_id = None
        for candidate in list(DEFERRED_JOB_ORDER):
            with JOBS_LOCK:
                job = JOBS.get(candidate)
                is_waiting = job is not None and job.status == "deferred"
            if candidate in DEFERRED_JOB_STARTERS and is_waiting:
                next_job_id = candidate
                DEFERRED_JOB_ORDER.remove(candidate)
                break
            DEFERRED_JOB_STARTERS.pop(candidate, None)
            DEFERRED_JOB_ORDER.remove(candidate)
    if next_job_id is None:
        return None
    update_job(
        next_job_id,
        status="queued",
        message="Starting deferred job",
        current=None,
        started_at=perf_counter(),
    )
    Thread(target=_run_deferred_job, args=(next_job_id,), daemon=True).start()
    logger.info("Started deferred job job_id=%s", next_job_id)
    return next_job_id


def sync_memory_jobs_from_durable_jobs(jobs: list[object]) -> None:
    with JOBS_LOCK:
        for durable_job in jobs:
            memory_job = JOBS.get(durable_job.id)
            if memory_job is None:
                continue
            memory_job.status = durable_job.status
            memory_job.message = durable_job.message
            memory_job.total = durable_job.total
            memory_job.done = durable_job.done
            memory_job.failed = durable_job.failed
            if durable_job.status not in {"queued", "running"}:
                memory_job.current = None
                memory_job.eta_seconds = None
                if memory_job.finished_at is None:
                    memory_job.finished_at = perf_counter()


# schedule_auto_index_for_analysis stays in main.py because it depends on
# _index_job which is part of the analysis pipeline (will move to services/analysis.py).
