"""Background maintenance loop.

Extracted from app/main.py — Stage 6f.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from threading import Thread
from time import monotonic

import app.state as _state
from app.api.deps import context
from app.config import Settings
from app.services.jobs import maybe_start_next_deferred_job, sync_memory_jobs_from_durable_jobs

logger = logging.getLogger(__name__)

_ALBUMS_FOR_YOU_REFRESH_HOURS = 6.0
_FLOW_REFRESH_HOURS = 6.0
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
    if settings.auth.enabled:
        # Session-bound user credentials do not exist in background work.
        # Importing service-account state into one user would leak preferences.
        return
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


def _maybe_refresh_generated_mixes(store, settings) -> None:
    try:
        from app.mixes import ensure_dashboard_mixes  # noqa: PLC0415
        from app.services.dashboard import _generated_mix_settings  # noqa: PLC0415
        ensure_dashboard_mixes(store, settings, _generated_mix_settings(settings))
    except Exception:
        logger.exception("generated mixes background refresh failed user_id=%s", store.user_id)


def _maybe_refresh_flow_profile(store, settings) -> None:
    try:
        profile = store.get_flow_profile(settings.default_model)
        if profile is not None and profile.last_built_at:
            built = datetime.fromisoformat(profile.last_built_at)
            if built.tzinfo is None:
                built = built.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - built).total_seconds() / 3600.0
            if age_hours < _FLOW_REFRESH_HOURS:
                return
        from app.services.flow_regions import FlowSettings, rebuild_flow_profile  # noqa: PLC0415
        rebuild_flow_profile(
            store,
            settings,
            FlowSettings(model_key=settings.default_model),
        )
    except Exception:
        logger.exception("flow profile background refresh failed user_id=%s", store.user_id)


def run_maintenance_tick(store=None) -> None:
    if store is None:
        store, settings = context()
    else:
        settings = Settings.from_env()
    store.expire_analysis_leases()
    store.refresh_active_analysis_jobs()
    sync_memory_jobs_from_durable_jobs(store.recent_analysis_jobs(limit=100))
    maybe_start_next_deferred_job()
    user_id = getattr(store, "user_id", None)
    if user_id is not None or not hasattr(store, "list_user_ids"):
        user_stores = [store]
    else:
        user_stores = [store.for_user(uid) for uid in store.list_user_ids()]
    for user_store in user_stores:
        _maybe_refresh_albums_for_you(user_store, settings)
        _maybe_refresh_generated_mixes(user_store, settings)
        _maybe_refresh_flow_profile(user_store, settings)
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
