"""Built-in user avatars (social features, plans/social-spec.md §2.4).

Users never upload images: an avatar is one key from this fixed whitelist,
stored in ``user_settings`` under ``AVATAR_SETTING_KEY``. The files live in the
UI bundle as ``ui/src/assets/avatars/<key>.webp``; a test keeps the two in
sync. Adding an avatar = add the file + its key here.
"""
from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.store import Store

AVATAR_KEYS: tuple[str, ...] = tuple(f"a{index:02d}" for index in range(1, 20))  # a01…a19
AVATAR_SETTING_KEY = "avatar"


def is_avatar_key(value: object) -> bool:
    return isinstance(value, str) and value in AVATAR_KEYS


def random_avatar_key() -> str:
    return secrets.choice(AVATAR_KEYS)


def ensure_user_avatar(store: "Store", user_id: int) -> str:
    """Return the user's avatar key, assigning a random one if none is set.

    Assignment happens once: concurrent first reads converge on whichever key
    was written first, and a valid choice is never replaced. A stored key that
    left the whitelist is replaced by a fresh random one.
    """
    current = store.get_user_avatar(user_id)
    if is_avatar_key(current):
        return str(current)
    return store.assign_default_avatar(
        user_id,
        random_avatar_key(),
        replace_value=current,
    )
