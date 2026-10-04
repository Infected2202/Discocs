"""Users and avatars API (social features Ф1, plans/social-spec.md §2.4/§3).

``GET /users`` lists every discocs user with only the public fields
(username + avatar). ``PUT /me/avatar`` changes the caller's own avatar —
there is no way to address another user. Both require a signed-in user: a
service principal has no ``user_id`` and is refused like other personal
endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.api.deps import api_error, context
from app.avatars import ensure_user_avatar
from app.schemas.requests import AvatarUpdateRequest

router = APIRouter(prefix="/api/v1")

_USER_REQUIRED = "A signed-in user is required"


@router.get("/users", response_model=None)
def api_v1_list_users() -> dict[str, object] | JSONResponse:
    store, _settings = context()
    if store.user_id is None:
        return api_error(403, "forbidden", _USER_REQUIRED)
    return {
        "items": [
            {
                "username": str(row["navidrome_username"]),
                "avatar": ensure_user_avatar(store, int(row["id"])),
            }
            for row in store.list_users()
        ]
    }


@router.put("/me/avatar", response_model=None)
def api_v1_set_my_avatar(request: AvatarUpdateRequest) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    if store.user_id is None:
        return api_error(403, "forbidden", _USER_REQUIRED)
    return {"avatar": store.set_own_avatar(request.key)}
