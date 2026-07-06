"""Dashboard API routes.

Extracted from app/main.py — Stage 6b.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.api.deps import api_error, context
from app.services.dashboard import dashboard_shelf_response, ensure_dashboard_mixes_fast

router = APIRouter()


@router.get("/api/v1/dashboard", response_model=None)
def api_v1_dashboard(
    limit: Annotated[int, Query(ge=1, le=50)] = 12,
    include_debug: bool = False,
) -> dict[str, object]:
    store, settings = context()
    ensure_diagnostics = ensure_dashboard_mixes_fast(store, settings)
    shelf_keys = ["mixes_for_you", "albums_for_you", "liked_artists", "new_releases", "recently_added", "listen_again", "long_time_no_listen", "discover_random", "history", "liked_releases"]
    shelves = [
        shelf
        for key in shelf_keys
        if (shelf := dashboard_shelf_response(store, key, limit=limit, offset=0, include_debug=include_debug)) is not None
    ]
    flow_profile = store.get_flow_profile(settings.default_model)
    flow_available = flow_profile is not None and flow_profile.status == "ready"
    return {
        "hero": {
            "type": "flow",
            "title": "Flow",
            "subtitle": "Start your personal stream" if flow_available else "Build your profile to start",
            "available": flow_available,
            "action": {
                "type": "start_flow",
                "enabled": flow_available,
                "endpoint": "/api/v1/flow/start" if flow_available else None,
            },
        },
        "shelves": shelves,
        "settings": {
            "visible_shelves": shelf_keys,
            "items_per_shelf": limit,
        },
        **({"generation": {"mixes_for_you": ensure_diagnostics}} if include_debug else {}),
    }


@router.get("/api/v1/dashboard/shelves/{key}", response_model=None)
def api_v1_dashboard_shelf(
    key: str,
    limit: Annotated[int, Query(ge=1, le=50)] = 12,
    offset: Annotated[int, Query(ge=0)] = 0,
    include_debug: bool = False,
) -> dict[str, object] | JSONResponse:
    store, settings = context()
    ensure_result = None
    if key == "mixes_for_you":
        ensure_result = ensure_dashboard_mixes_fast(store, settings)
    shelf = dashboard_shelf_response(store, key, limit=limit, offset=offset, include_debug=include_debug)
    if shelf is None:
        return api_error(404, "not_found", "Dashboard shelf not found")
    if include_debug and ensure_result is not None:
        shelf["generation"] = ensure_result
    return shelf
