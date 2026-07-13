"""Auth core for the Phase 1 access gate.

Model: Navidrome is the identity provider. Login verifies the submitted
username/password against the configured Navidrome server (Subsonic ``ping``);
on success an opaque server-side session is created. The password is retained
only as authenticated ciphertext whose key requires the raw client token.

Session tokens are 256-bit random values stored as SHA-256 hashes. AES-GCM
encrypts the per-user Navidrome password with a key derived from the raw token,
which remains only in the HttpOnly client cookie.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
import time
from dataclasses import dataclass, replace
from datetime import timedelta

from app.config import Settings
from app.navidrome import NavidromeClient, parse_song
from app.navidrome_starred import build_starred_track_ids_from_songs
from app.models import utc_now
from app.session_crypto import decrypt_nav_secret, encrypt_nav_secret
from app.store import Store

logger = logging.getLogger(__name__)

_TOKEN_BYTES = 32  # 256-bit opaque session token


@dataclass(frozen=True)
class ResolvedSession:
    """Identity behind a valid session: internal user id + Navidrome username.

    ``user_id`` may be None only for a legacy session created before the
    ``users`` table existed and whose username has no ``users`` row yet.
    """

    user_id: int | None
    username: str
    navidrome_password: str | None = None


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
    password: str | None = None,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> str:
    """Create a session and return the raw token (only the hash is stored).

    Login is the identity boundary: upsert the ``users`` row for this Navidrome
    username (creating it on first login, bumping ``last_login_at`` otherwise)
    and bind the resulting internal ``user_id`` to the session.
    """
    token = generate_token()
    created = utc_now()
    expires = _iso_plus_hours(created, settings.auth.session_ttl_hours)
    user_id = store.upsert_user(username, now=created)
    store.create_session(
        token_hash=hash_token(token),
        username=username,
        user_id=user_id,
        created_at=created,
        expires_at=expires,
        ip=ip,
        user_agent=(user_agent or "")[:512] or None,
        nav_secret=encrypt_nav_secret(token, password) if password else None,
    )
    return token


def resolve_session(store: Store, token: str | None) -> ResolvedSession | None:
    """Return the identity for a valid, unexpired session, else None.

    Expired sessions are deleted lazily on lookup. ``user_id`` comes from the
    session row; for a legacy session (NULL ``user_id``) it is resolved via the
    users table so callers still get an id whenever the user exists.
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
    username = str(row["username"])
    raw_user_id = row["user_id"]
    if raw_user_id is None:
        user_row = store.get_user_by_username(username)
        raw_user_id = user_row["id"] if user_row is not None else None
    user_id = int(raw_user_id) if raw_user_id is not None else None
    password = None
    secret = row["nav_secret"]
    if secret:
        try:
            password = decrypt_nav_secret(token, str(secret))
        except Exception:  # noqa: BLE001 — corrupt/tampered secret stays unusable
            logger.warning("Session Navidrome secret rejected user=%s", username)
    return ResolvedSession(
        user_id=user_id,
        username=username,
        navidrome_password=password,
    )


def revoke_session(store: Store, token: str | None) -> None:
    if token:
        store.delete_session(hash_token(token))


def sync_navidrome_starred_for_user(
    store: Store,
    settings: Settings,
    user_id: int,
    username: str,
    password: str,
    *,
    client_factory=NavidromeClient,
) -> None:
    """Import Navidrome stars into only the authenticated user's preferences."""
    nav = replace(settings.navidrome, user=username, password=password, auth_mode="token")
    starred = client_factory(nav).get_starred_full()
    scoped = store.for_user(user_id)
    songs = [parse_song(raw) for raw in starred["songs"]]
    mapped = build_starred_track_ids_from_songs(scoped, songs, user=username)
    scoped.sync_track_liked_from_navidrome(mapped["track_ids"])
    artist_ids = []
    for raw in starred["artists"]:
        external_id = raw.get("id")
        if not external_id:
            continue
        artist_id = scoped.entity_id_for_external_id(
            "navidrome", "artist", str(external_id)
        )
        if artist_id is not None:
            artist_ids.append(artist_id)
    scoped.sync_artist_liked_from_navidrome(artist_ids)


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
