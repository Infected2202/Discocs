"""Background maintenance loop.

Extracted from app/main.py — Stage 6f.
"""
from __future__ import annotations

import logging
from threading import Thread

import app.state as _state
from app.api.deps import context
from app.services.jobs import maybe_start_next_deferred_job, sync_memory_jobs_from_durable_jobs

logger = logging.getLogger(__name__)

_ALBUMS_FOR_YOU_REFRESH_HOURS = 6.0


def _maybe_refresh_albums_for_you(store, settings) -> None:
    try:
        model_name = settings.default_model
        age = store.albums_for_you_cache_age_hours(model_name)
        if age is not None and age < _ALBUMS_FOR_YOU_REFRESH_HOURS:
            return
        from app.services.albums_for_you import refresh_albums_for_you  # noqa: PLC0415
        refresh_albums_for_you(store, model_name)
    except Exception:
        logger.exception("albums_for_you background refresh failed")


def run_maintenance_tick(store=None) -> None:
    store, settings = context()
    store.expire_analysis_leases()
    store.refresh_active_analysis_jobs()
    sync_memory_jobs_from_durable_jobs(store.recent_analysis_jobs(limit=100))
    maybe_start_next_deferred_job()
    _maybe_refresh_albums_for_you(store, settings)


def maintenance_loop() -> None:
    while not _state.MAINTENANCE_STOP.wait(15):
        if _state.SHUTDOWN_REQUESTED:
            return
        try:
            run_maintenance_tick()
        except Exception:
            logger.exception("Background maintenance tick failed")


def start_maintenance_loop() -> None:
    _state.MAINTENANCE_STOP.clear()
    Thread(target=maintenance_loop, name="discocs-maintenance", daemon=True).start()
