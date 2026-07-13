"""Settings API routes (Navidrome + instant-mix settings).

Extracted from app/main.py — Stage 6b.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.api.deps import api_error, context, instant_mix_settings, request_field_names
from app.config import load_runtime_settings, save_runtime_settings
from app.logging_config import get_navidrome_plugin_logger
from app.navidrome import NavidromeClient
from app.schemas.requests import (
    InstantMixSettingsRequest,
    NavidromePluginEventRequest,
    NavidromeSettingsRequest,
    UserSettingsPatchRequest,
)

logger = logging.getLogger(__name__)
navidrome_logger = logging.getLogger("discocs.navidrome")
navidrome_plugin_logger = get_navidrome_plugin_logger()

router = APIRouter(prefix="/api/v1")


# ---------------------------------------------------------------------------
# Navidrome connection settings
# ---------------------------------------------------------------------------

@router.get("/settings/navidrome")
def get_navidrome_settings() -> dict[str, object]:
    _store, settings = context()
    nav = settings.navidrome
    return {
        "url": nav.url,
        "user": nav.user,
        "password_set": bool(nav.password),
        "auth_mode": nav.auth_mode,
        "timeout_seconds": nav.timeout_seconds,
        "download_mode": nav.download_mode,
        "temp_dir": str(nav.temp_dir),
    }


@router.put("/settings/navidrome")
def update_navidrome_settings(request: NavidromeSettingsRequest) -> dict[str, object]:
    _store, settings = context()
    saved = load_runtime_settings(settings.data_dir)
    existing = saved.get("navidrome", {})
    existing_password = existing.get("password", "") if isinstance(existing, dict) else ""
    password = request.password if request.password is not None else str(existing_password)
    saved["navidrome"] = {
        "url": request.url.strip(),
        "user": request.user.strip(),
        "password": password,
        "auth_mode": request.auth_mode,
        "timeout_seconds": request.timeout_seconds,
        "download_mode": request.download_mode,
        "temp_dir": request.temp_dir.strip()
        if request.temp_dir
        else str(settings.data_dir / "tmp" / "navidrome"),
    }
    save_runtime_settings(settings.data_dir, saved)
    navidrome_logger.info(
        "Saved Navidrome settings url=%s user=%s auth_mode=%s timeout_seconds=%s download_mode=%s password_set=%s",
        request.url,
        request.user,
        request.auth_mode,
        request.timeout_seconds,
        request.download_mode,
        bool(password),
    )
    return get_navidrome_settings()


@router.post("/navidrome/ping", responses={502: {"description": "Navidrome ping failed"}})
def ping_navidrome() -> dict[str, object]:
    _store, settings = context()
    try:
        payload = NavidromeClient(settings.navidrome).ping()
    except Exception as exc:
        navidrome_logger.warning("Navidrome ping failed", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Navidrome ping failed: {exc}") from exc
    return {
        "status": "ok",
        "version": payload.get("version", ""),
        "server_version": payload.get("serverVersion", ""),
    }


@router.post("/navidrome/plugin-event")
def record_navidrome_plugin_event(request: NavidromePluginEventRequest) -> dict[str, str]:
    navidrome_plugin_logger.info(
        "plugin_event event=%s item_id=%s model=%s count=%s status=%s discocs_url=%s message=%s",
        request.event,
        request.item_id,
        request.model,
        request.count,
        request.status,
        request.discocs_url,
        request.message,
    )
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Per-user settings (language, and future UI/behavior preferences)
# ---------------------------------------------------------------------------

@router.get("/me/settings")
def get_user_settings() -> dict[str, object]:
    store, _settings = context()
    return store.get_user_settings()


@router.patch("/me/settings")
def update_user_settings(request: UserSettingsPatchRequest) -> dict[str, object]:
    store, _settings = context()
    fields = request_field_names(request)
    values = {
        name: value
        for name, value in request.model_dump().items()
        if name in fields and value is not None
    }
    if not values:
        return store.get_user_settings()
    return store.set_user_settings(values)


# ---------------------------------------------------------------------------
# Instant-mix settings
# ---------------------------------------------------------------------------

@router.get("/instant-mix/settings")
def get_instant_mix_settings() -> dict[str, object]:
    _store, settings = context()
    return instant_mix_settings(settings)


@router.put("/instant-mix/settings")
def update_instant_mix_settings(request: InstantMixSettingsRequest) -> dict[str, object]:
    _store, settings = context()
    saved = load_runtime_settings(settings.data_dir)
    saved["instant_mix"] = {
        "model": request.model,
        "count": request.count,
        "min_similarity": request.min_similarity,
        "max_per_artist": request.max_per_artist,
        "exclude_same_album": request.exclude_same_album,
        "count_collaboration_artists": request.count_collaboration_artists,
    }
    save_runtime_settings(settings.data_dir, saved)
    return instant_mix_settings(settings)
