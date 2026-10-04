"""Profile access layer: read another user's profile without weakening default-deny.

Social features (plans/social-spec.md §2.2). Every logged-in user may view every
profile, but the viewer's own ``Store`` stays bound to the viewer. This module
resolves ``username → user_id`` (case-insensitively, the same canonicalization
as ``users``), builds a store bound to the *target* via ``Store.for_user`` and
calls only an explicit set of read methods on it, returning whitelisted fields.

Rules:
* the viewer must be a user — a service principal (store without ``user_id``)
  is refused with ``ProfileViewerRequiredError``;
* an unknown username raises ``ProfileNotFoundError`` (HTTP 404);
* private playlists are visible only when viewer == target;
* never exposed: flow profile, playback sessions/queue, ``user_settings``
  (except the avatar key), Navidrome credentials, preference scores/dislikes.

The HTTP endpoints arrive in Ф3; the API layer maps the two errors to 403/404.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.avatars import ensure_user_avatar
from app.models import Playlist
from app.store import Store


class ProfileNotFoundError(LookupError):
    """No discocs user with that username."""


class ProfileViewerRequiredError(PermissionError):
    """Profiles are read on behalf of a signed-in user only."""


@dataclass(frozen=True)
class ProfileTarget:
    user_id: int
    username: str
    created_at: str
    # Bound to the profile owner: personal reads return *their* data.
    store: Store


def viewer_user_id(viewer_store: Store) -> int:
    if viewer_store.user_id is None:
        raise ProfileViewerRequiredError("A signed-in user is required to view profiles")
    return int(viewer_store.user_id)


def resolve_profile_target(viewer_store: Store, username: str) -> ProfileTarget:
    viewer_user_id(viewer_store)
    clean = (username or "").strip()
    row = viewer_store.get_user_by_username(clean) if clean else None
    if row is None:
        raise ProfileNotFoundError(f"User not found: {username}")
    target_id = int(row["id"])
    return ProfileTarget(
        user_id=target_id,
        username=str(row["navidrome_username"]),
        created_at=str(row["created_at"]),
        store=viewer_store.for_user(target_id),
    )


def can_view_private_playlists(viewer_id: int | None, target_id: int) -> bool:
    return viewer_id is not None and viewer_id == target_id


def profile_header(viewer_store: Store, username: str) -> dict[str, object]:
    """Whitelisted header fields of a profile (§1.3 «Шапка»)."""
    viewer_id = viewer_user_id(viewer_store)
    target = resolve_profile_target(viewer_store, username)
    return {
        "username": target.username,
        "avatar": ensure_user_avatar(viewer_store, target.user_id),
        "created_at": target.created_at,
        "viewer_is_owner": viewer_id == target.user_id,
    }


def profile_playlists(viewer_store: Store, username: str) -> list[Playlist]:
    """The target's own playlists; private ones only on their own profile."""
    viewer_id = viewer_user_id(viewer_store)
    target = resolve_profile_target(viewer_store, username)
    return target.store.list_owned_playlists(
        include_private=can_view_private_playlists(viewer_id, target.user_id),
    )
