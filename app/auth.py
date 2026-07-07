"""Auth core for the Phase 1 access gate.

Model: Navidrome is the identity provider. Login verifies the submitted
username/password against the configured Navidrome server (Subsonic ``ping``);
on success an opaque server-side session is created. The password is used only
to verify and is then discarded — it is never stored.

No third-party crypto dependency: session tokens are 256-bit random values from
``secrets`` and are stored as SHA-256 hashes; comparisons use ``hmac`` constant
time. See app/store/base.py for the ``sessions`` table.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
import time
from dataclasses import replace
from datetime import timedelta

from app.config import Settings
from app.navidrome import NavidromeClient
from app.models import utc_now
from app.store import Store

logger = logging.getLogger(__name__)

_TOKEN_BYTES = 32  # 256-bit opaque session token


def generate_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def verify_navidrome_credentials(
    settings: Settings,
    username: str,
    password: str,
    *,
    client_factory=NavidromeClient,
) -> bool:
    """Return True iff (username, password) authenticate against Navidrome.

    URL is taken from server config (the login form never supplies it). Uses
    token auth (salted MD5) so the plaintext password is not put on the wire.
    """
    if not username or not password:
        return False
    nav = replace(
        settings.navidrome,
        user=username,
        password=password,
        auth_mode="token",
    )
    try:
        client = client_factory(nav)
        client.ping()
    except Exception as exc:  # noqa: BLE001 — any failure means "not authenticated"
        logger.info("Navidrome credential check failed user=%s error=%s", username, exc)
        return False
    return True


def create_session(
    store: Store,
    settings: Settings,
    username: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> str:
    """Create a session and return the raw token (only the hash is stored)."""
    token = generate_token()
    created = utc_now()
    expires = _iso_plus_hours(created, settings.auth.session_ttl_hours)
    store.create_session(
        token_hash=hash_token(token),
        username=username,
        created_at=created,
        expires_at=expires,
        ip=ip,
        user_agent=(user_agent or "")[:512] or None,
    )
    return token


def resolve_session(store: Store, token: str | None) -> str | None:
    """Return the username for a valid, unexpired session, else None.

    Expired sessions are deleted lazily on lookup.
    """
    if not token:
        return None
    token_hash = hash_token(token)
    row = store.get_session(token_hash)
    if row is None:
        return None
    now = utc_now()
    if str(row["expires_at"]) <= now:
        store.delete_session(token_hash)
        return None
    store.touch_session(token_hash, now)
    return str(row["username"])


def revoke_session(store: Store, token: str | None) -> None:
    if token:
        store.delete_session(hash_token(token))


def _iso_plus_hours(iso_now: str, hours: int) -> str:
    from datetime import datetime

    base = datetime.fromisoformat(iso_now)
    return (base + timedelta(hours=hours)).isoformat()


class LoginRateLimiter:
    """In-memory per-IP login throttle. No Redis (per project constraints).

    Sliding-window failure counter with a hard lockout once the threshold is hit
    inside the window. Successful logins clear the counter for that IP.
    """

    def __init__(self, max_attempts: int, window_seconds: int):
        self._max_attempts = max_attempts
        self._window = window_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, ip: str, now: float) -> list[float]:
        recent = [t for t in self._failures.get(ip, []) if now - t < self._window]
        if recent:
            self._failures[ip] = recent
        else:
            self._failures.pop(ip, None)
        return recent

    def is_locked(self, ip: str, *, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            return len(self._prune(ip, now)) >= self._max_attempts

    def record_failure(self, ip: str, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            recent = self._prune(ip, now)
            recent.append(now)
            self._failures[ip] = recent

    def record_success(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)
