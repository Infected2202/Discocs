"""Background maintenance loop.

Extracted from app/main.py — Stage 6f.
"""
from __future__ import annotations

import logging
from threading import Thread
from time import monotonic

import app.state as _state
from app.api.deps import context
from app.config import Settings
from app.services.jobs import maybe_start_next_deferred_job, sync_memory_jobs_from_durable_jobs

logger = logging.getLogger(__name__)

_ALBUMS_FOR_YOU_REFRESH_HOURS = 6.0
_SESSION_PURGE_INTERVAL_SECONDS = 3600.0

# Monotonic timestamp of the last Navidrome play-state refresh (throttling).
_last_play_state_refresh: float | None = None
# Monotonic timestamp of the last expired-session purge (throttling).
_last_session_purge: float | None = None


def _maybe_purge_expired_sessions(store) -> None:
    global _last_session_purge
    now = monotonic()
    if (
        _last_session_purge is not None
        and now - _last_session_purge < _SESSION_PURGE_INTERVAL_SECONDS
    ):
        return
    _last_session_purge = now
    try:
        from app.models import utc_now  # noqa: PLC0415
        store.purge_expired_sessions(utc_now())
    except Exception:
        logger.exception("Expired-session purge failed")


def _maybe_refresh_navidrome_play_state(store, settings) -> None:
    global _last_play_state_refresh
    nav = settings.navidrome
    interval = getattr(nav, "play_state_refresh_seconds", 0)
    if interval <= 0 or not nav.url:
        return
    now = monotonic()
    if _last_play_state_refresh is not None and now - _last_play_state_refresh < interval:
        return
    _last_play_state_refresh = now
    try:
        from app.navidrome import NavidromeClient  # noqa: PLC0415
        from app.navidrome_sync import refresh_navidrome_play_state  # noqa: PLC0415
        client = NavidromeClient(nav)
        refresh_navidrome_play_state(
            store, client, album_count=nav.play_state_refresh_albums,
        )
    except Exception:
        logger.exception("Navidrome play-state background refresh failed")


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
    if store is None:
        store, settings = context()
    else:
        settings = Settings.from_env()
    store.expire_analysis_leases()
    store.refresh_active_analysis_jobs()
    sync_memory_jobs_from_durable_jobs(store.recent_analysis_jobs(limit=100))
    maybe_start_next_deferred_job()
    _maybe_refresh_albums_for_you(store, settings)
    _maybe_refresh_navidrome_play_state(store, settings)
    _maybe_purge_expired_sessions(store)


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
