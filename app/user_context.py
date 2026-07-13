"""Request-local identity used to construct user-scoped stores."""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

_UNSET = object()
CURRENT_USER_ID: ContextVar[int | None | object] = ContextVar(
    "discocs_current_user_id", default=_UNSET
)


@dataclass(frozen=True)
class NavidromeCredentials:
    username: str
    password: str


CURRENT_NAVIDROME_CREDENTIALS: ContextVar[NavidromeCredentials | None] = ContextVar(
    "discocs_current_navidrome_credentials", default=None
)


def set_current_user_id(user_id: int | None) -> Token[int | None | object]:
    return CURRENT_USER_ID.set(user_id)


def reset_current_user_id(token: Token[int | None | object]) -> None:
    CURRENT_USER_ID.reset(token)


def current_user_id() -> tuple[bool, int | None]:
    """Return ``(identity_was_set, user_id)`` for the current request."""
    value = CURRENT_USER_ID.get()
    if value is _UNSET:
        return False, None
    return True, int(value) if value is not None else None


def set_current_navidrome_credentials(
    credentials: NavidromeCredentials | None,
) -> Token[NavidromeCredentials | None]:
    return CURRENT_NAVIDROME_CREDENTIALS.set(credentials)


def reset_current_navidrome_credentials(
    token: Token[NavidromeCredentials | None],
) -> None:
    CURRENT_NAVIDROME_CREDENTIALS.reset(token)


def current_navidrome_credentials() -> NavidromeCredentials | None:
    return CURRENT_NAVIDROME_CREDENTIALS.get()
