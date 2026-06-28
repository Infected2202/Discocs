"""Dashboard API routes.

Extracted from app/main.py — Stage 6b.
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.api.deps import api_error, context
from app.services.dashboard import dashboard_shelf_response, ensure_dashboard_mixes_fast

router = APIRouter()


@router.get("/api/v1/dashboard", response_model=None)
def api_v1_dashboard(
    limit: int = Query(default=12, ge=1, le=50),
    include_debug: bool = False,
) -> dict[str, object]:
    store, settings = context()
    ensure_diagnostics = ensure_dashboard_mixes_fast(store, settings)
    shelf_keys = ["mixes_for_you", "recently_added", "discover_random", "listen_again", "long_time_no_listen"]
    shelves = [
        shelf
        for key in shelf_keys
        if (shelf := dashboard_shelf_response(store, key, limit=limit, offset=0, include_debug=include_debug)) is not None
    ]
    return {
        "hero": {
            "type": "flow",
            "title": "Flow",
            "subtitle": "Start your personal stream",
            "available": False,
            "action": {"type": "start_flow", "enabled": False, "endpoint": None},
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
    limit: int = Query(default=12, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
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
