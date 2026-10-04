"""Social API: who else uses discocs and what they play now (social Ф2).

``GET /social/people`` — every discocs user (the caller too), with avatar and
live "now playing" from Navidrome (service account, cached ~5 s). See
docs/social.md and ``app/services/presence.py``.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.api.deps import api_error, context
from app.services.presence import people

router = APIRouter(prefix="/api/v1")


@router.get("/social/people", response_model=None)
def api_v1_social_people() -> dict[str, object] | JSONResponse:
    store, settings = context()
    if store.user_id is None:
        return api_error(403, "forbidden", "A signed-in user is required")
    return {"items": people(store, settings)}
