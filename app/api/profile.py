"""Profile API (social features Ф3, plans/social-spec.md §1.3/§2.2/§3).

Every signed-in user may read every profile. All reads go through
app/services/profile.py, which binds a store to the *target* user and returns
only whitelisted fields; private playlists are listed only to their owner.
A service principal (no ``user_id``) gets 403, an unknown username 404; the
username is matched case-insensitively. Contract: docs/social.md.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.api.deps import api_error, context
from app.services.profile import (
    DEFAULT_PROFILE_PERIOD,
    ProfileNotFoundError,
    ProfileViewerRequiredError,
    profile_likes,
    profile_listens,
    profile_playlists_payload,
    profile_stats,
)

router = APIRouter(prefix="/api/v1")

ProfilePeriod = Literal["7d", "30d", "90d", "180d", "365d", "all"]

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    403: {"description": "A signed-in user is required (service principal)"},
    404: {"description": "Unknown username"},
}


def _profile_response(build: Callable[[], dict[str, object]]) -> dict[str, object] | JSONResponse:
    try:
        return build()
    except ProfileViewerRequiredError as exc:
        return api_error(403, "forbidden", str(exc))
    except ProfileNotFoundError as exc:
        return api_error(404, "not_found", str(exc))


@router.get("/users/{username}/profile", response_model=None, responses=_ERROR_RESPONSES)
def api_v1_user_profile(
    username: str,
    period: Annotated[ProfilePeriod, Query()] = DEFAULT_PROFILE_PERIOD,
    tz: Annotated[str | None, Query(description="IANA timezone; invalid or missing → UTC")] = None,
) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    return _profile_response(lambda: profile_stats(store, username, period=period, tz_name=tz))


@router.get("/users/{username}/listens", response_model=None, responses=_ERROR_RESPONSES)
def api_v1_user_listens(
    username: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    return _profile_response(lambda: profile_listens(store, username, limit=limit, offset=offset))


@router.get("/users/{username}/likes", response_model=None, responses=_ERROR_RESPONSES)
def api_v1_user_likes(
    username: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    return _profile_response(lambda: profile_likes(store, username, limit=limit, offset=offset))


@router.get("/users/{username}/playlists", response_model=None, responses=_ERROR_RESPONSES)
def api_v1_user_playlists(username: str) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    return _profile_response(lambda: profile_playlists_payload(store, username))
