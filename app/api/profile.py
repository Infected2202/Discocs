"""Profile API (social features Ф3, plans/social-spec.md §1.3/§2.2/§3).

Every signed-in user may read every profile. All reads go through
app/services/profile.py, which binds a store to the *target* user and returns
only whitelisted fields; private playlists are listed only to their owner.
A service principal (no ``user_id``) gets 403, an unknown username 404; the
username is matched case-insensitively. Contract: docs/social.md.

Horizontal profile shelves (tops, likes, playlists) preview
``SHELF_PREVIEW_LIMIT`` cards; their full lists page through
``/users/{username}/top/{kind}``, ``/users/{username}/likes/{kind}`` and
``/users/{username}/playlists`` with ``limit``/``offset``.

``POST /users/{username}/listen-along`` (Ф6) starts the viewer's own session
from what that user plays right now — see app/services/listen_along.py.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.api.deps import api_error, context, playback_session_settings
from app.serializers.playback import playback_session_response
from app.services.listen_along import (
    ListenAlongSelfError,
    ListenAlongUnavailableError,
    listen_along,
)
from app.services.profile import (
    DEFAULT_PROFILE_PERIOD,
    ProfileNotFoundError,
    ProfileViewerRequiredError,
    profile_likes,
    profile_likes_of_kind,
    profile_listens,
    profile_playlists_payload,
    profile_stats,
    profile_top,
)
from app.services.shelves import FULL_LIST_MAX_LIMIT

router = APIRouter(prefix="/api/v1")

ProfilePeriod = Literal["7d", "30d", "90d", "180d", "365d", "all"]
TopKind = Literal["artists", "releases"]
LikeKind = Literal["tracks", "releases", "artists"]
PageLimit = Annotated[int, Query(ge=1, le=FULL_LIST_MAX_LIMIT)]
PageOffset = Annotated[int, Query(ge=0)]

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


@router.get("/users/{username}/top/{kind}", response_model=None, responses=_ERROR_RESPONSES)
def api_v1_user_top(
    username: str,
    kind: TopKind,
    period: Annotated[ProfilePeriod, Query()] = DEFAULT_PROFILE_PERIOD,
    tz: Annotated[str | None, Query(description="IANA timezone; invalid or missing → UTC")] = None,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> dict[str, object] | JSONResponse:
    """Full top artists/releases of a period — the profile's top shelves, paged."""
    store, _settings = context()
    return _profile_response(
        lambda: profile_top(store, username, kind, period=period, tz_name=tz, limit=limit, offset=offset)
    )


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


@router.get("/users/{username}/likes/{kind}", response_model=None, responses=_ERROR_RESPONSES)
def api_v1_user_likes_of_kind(
    username: str,
    kind: LikeKind,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> dict[str, object] | JSONResponse:
    """One kind of likes (tracks/releases/artists), page by page."""
    store, _settings = context()
    return _profile_response(
        lambda: profile_likes_of_kind(store, username, kind, limit=limit, offset=offset)
    )


@router.get("/users/{username}/playlists", response_model=None, responses=_ERROR_RESPONSES)
def api_v1_user_playlists(
    username: str,
    limit: Annotated[int | None, Query(ge=1, le=FULL_LIST_MAX_LIMIT, description="Omitted → all")] = None,
    offset: PageOffset = 0,
) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    return _profile_response(
        lambda: profile_playlists_payload(store, username, limit=limit, offset=offset)
    )


_LISTEN_ALONG_RESPONSES: dict[int | str, dict[str, object]] = {
    **_ERROR_RESPONSES,
    400: {"description": "Listening along with yourself"},
    409: {"description": "The user is not playing, or the track is not in the library"},
}


@router.post("/users/{username}/listen-along", response_model=None, responses=_LISTEN_ALONG_RESPONSES)
def api_v1_user_listen_along(username: str) -> dict[str, object] | JSONResponse:
    """One-shot "pick up" of what ``username`` plays now (docs/social.md).

    Creates the caller's own playback session (``source_type =
    "listen_along"``) and returns the usual session envelope plus where to
    start: ``start_track_id``/``start_queue_item_id`` and
    ``start_position_seconds``.
    """
    store, settings = context()
    try:
        start = listen_along(
            store,
            settings,
            username,
            session_settings=playback_session_settings({}),
        )
    except ProfileViewerRequiredError as exc:
        return api_error(403, "forbidden", str(exc))
    except ProfileNotFoundError as exc:
        return api_error(404, "not_found", str(exc))
    except ListenAlongSelfError as exc:
        return api_error(400, "invalid_request", str(exc))
    except ListenAlongUnavailableError as exc:
        return api_error(409, exc.code, str(exc))
    envelope = playback_session_response(store, start.session)
    return {
        **envelope,
        "start_track_id": start.start_track_id,
        "start_queue_item_id": start.queue[0].id if start.queue else None,
        "start_position_seconds": start.position_ms / 1000,
        "listen_along": {"host": start.host_username, "strategy": start.strategy},
    }
