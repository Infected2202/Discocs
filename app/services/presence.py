"""Presence: who is listening to what right now (social Ф2, plans/social-spec.md §2.3).

Write side — ``report_presence``: the logged-in player reports its state
(starting/playing/paused/stopped + position) and discocs forwards it to
Navidrome's OpenSubsonic ``reportPlayback`` with the *session's* Navidrome
credentials, ``ignoreScrobble=true``. Presence is not a playback event and
never touches ``playback_events``/``listens``/preferences. When the report
carries the caller's own ``session_id`` (Ф6), the state, position, track and
queue item are also stored on that ``playback_sessions`` row
(``presence_*`` columns) — regardless of the Navidrome outcome or mapping —
so listen-along can find where the host is, across restarts/deploys. Reports
come only on transitions, so the writes are cheap. Any Navidrome failure is
swallowed (``status: failed``) — presence must never break playback.

Read side — ``people``: every discocs user (the viewer too), with their
avatar and what they play now. Live data is one ``getNowPlaying`` call with
the *service* Navidrome account, cached in-process for a few seconds
(``NowPlayingCache``) so polling clients don't fan out to Navidrome.
"""
from __future__ import annotations

from dataclasses import replace
import logging
import math
import threading
import time
from typing import Callable

from app.avatars import ensure_user_avatar
from app.config import NavidromeSettings
from app.navidrome import NavidromeClient, NowPlayingEntry
from app.serializers.entities import track_summary_dict
from app.store import Store
from app.user_context import current_navidrome_credentials

logger = logging.getLogger("discocs.navidrome")

NOW_PLAYING_TTL_SECONDS = 5.0
# Presence calls sit next to interactive playback and the people shelf poll:
# never wait the full (sync-sized) Navidrome timeout for them.
PRESENCE_TIMEOUT_SECONDS = 5
# Entries without the OpenSubsonic ``state`` field come from older clients
# that only send scrobble(submission=false) — they are "playing" by definition.
ACTIVE_NOW_PLAYING_STATES = frozenset({"starting", "playing"})


def _with_short_timeout(nav: NavidromeSettings) -> NavidromeSettings:
    return replace(nav, timeout_seconds=min(int(nav.timeout_seconds), PRESENCE_TIMEOUT_SECONDS))


def _session_navidrome_settings(settings) -> NavidromeSettings | None:
    """Navidrome settings of the signed-in user (None when auth has no creds)."""
    credentials = current_navidrome_credentials()
    if credentials is None:
        if settings.auth.enabled:
            return None
        return settings.navidrome
    return replace(
        settings.navidrome,
        user=credentials.username,
        password=credentials.password,
        auth_mode="token",
    )


# ---------------------------------------------------------------------------
# Write: POST /playback/presence
# ---------------------------------------------------------------------------

def report_presence(
    store: Store,
    settings,
    *,
    track_id: int,
    state: str,
    position_ms: int,
    session_id: str | None = None,
    queue_item_id: str | None = None,
) -> dict[str, object]:
    if session_id:
        # Scoped to the caller: another user's session id matches no row and
        # is silently ignored (the player fires and forgets).
        store.record_playback_presence(
            session_id,
            track_id=track_id,
            state=state,
            position_ms=position_ms,
            queue_item_id=queue_item_id,
        )
    item_id = store.external_id_for_track("navidrome", track_id)
    if not item_id:
        return {"status": "skipped", "reason": "no_navidrome_mapping", "track_id": track_id}
    nav = _session_navidrome_settings(settings)
    if nav is None:
        return {"status": "skipped", "reason": "missing_user_credentials", "track_id": track_id}
    try:
        NavidromeClient(_with_short_timeout(nav)).report_playback(
            item_id,
            state=state,
            position_ms=position_ms,
            ignore_scrobble=True,
        )
    except Exception as exc:  # noqa: BLE001 — presence is best-effort by contract
        logger.warning(
            "Navidrome reportPlayback failed track_id=%s item_id=%s state=%s error=%s",
            track_id, item_id, state, exc,
        )
        return {"status": "failed", "track_id": track_id, "state": state}
    return {"status": "ok", "track_id": track_id, "state": state}


# ---------------------------------------------------------------------------
# Read: GET /social/people
# ---------------------------------------------------------------------------

NowPlayingLoader = Callable[[], list[NowPlayingEntry]]


class NowPlayingCache:
    """Thread-safe, TTL-bound memo of the last ``getNowPlaying`` result.

    Keyed by the Navidrome endpoint + service user so a settings change is
    never answered from a stale entry. Failures are cached too (as ``None``)
    for the same TTL: an unreachable Navidrome must not be retried — and
    waited on — by every poll of every client.
    """

    def __init__(
        self,
        ttl_seconds: float = NOW_PLAYING_TTL_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._key: tuple[str, str] | None = None
        self._expires_at = 0.0
        self._entries: list[NowPlayingEntry] | None = None

    def get(self, key: tuple[str, str], loader: NowPlayingLoader) -> list[NowPlayingEntry] | None:
        # Held across the load on purpose: concurrent pollers wait for one
        # Navidrome call instead of each issuing their own.
        with self._lock:
            now = self._clock()
            if self._key == key and now < self._expires_at:
                return self._entries
            try:
                entries: list[NowPlayingEntry] | None = loader()
            except Exception as exc:  # noqa: BLE001 — "nobody is playing" is the safe answer
                logger.warning("Navidrome getNowPlaying failed: %s", exc)
                entries = None
            self._key = key
            self._entries = entries
            self._expires_at = self._clock() + self.ttl_seconds
            return entries

    def clear(self) -> None:
        with self._lock:
            self._key = None
            self._entries = None
            self._expires_at = 0.0


_NOW_PLAYING_CACHE = NowPlayingCache()


def now_playing_entries(settings) -> list[NowPlayingEntry] | None:
    """Live ``getNowPlaying`` entries via the service account; None if unavailable."""
    nav: NavidromeSettings = settings.navidrome
    if not (nav.url and nav.user and nav.password):
        return None
    return _NOW_PLAYING_CACHE.get(
        (nav.url, nav.user),
        lambda: NavidromeClient(_with_short_timeout(nav)).get_now_playing(),
    )


def _is_active(entry: NowPlayingEntry) -> bool:
    return entry.state is None or entry.state in ACTIVE_NOW_PLAYING_STATES


def _freshest_by_username(entries: list[NowPlayingEntry]) -> dict[str, NowPlayingEntry]:
    """One active entry per lower-cased username — the most recent player wins."""
    best: dict[str, NowPlayingEntry] = {}
    for entry in entries:
        if not entry.username or not _is_active(entry):
            continue
        key = entry.username.casefold()
        current = best.get(key)
        age = entry.minutes_ago if entry.minutes_ago is not None else math.inf
        current_age = (
            current.minutes_ago
            if current is not None and current.minutes_ago is not None
            else math.inf
        )
        if current is None or age < current_age:
            best[key] = entry
    return best


def now_playing_for(settings, username: str) -> NowPlayingEntry | None:
    """The user's active ``getNowPlaying`` entry (cached), as the people shelf sees it."""
    entries = now_playing_entries(settings) or []
    return _freshest_by_username(entries).get(username.casefold())


def _now_playing_dict(store: Store, entry: NowPlayingEntry) -> dict[str, object]:
    track = store.get_track_by_external_id("navidrome", entry.id)
    if track is None:
        return {
            "track_id": None,
            "title": entry.title or "",
            "artists": entry.artist or "",
            "state": entry.state or "playing",
            "track": None,
        }
    artists = store.artists_for_tracks([track.id]).get(track.id, [])
    names = [artist.name for artist in artists]
    return {
        "track_id": track.id,
        "title": track.title or entry.title or "",
        "artists": ", ".join(names) or track.artist or entry.artist or "",
        "state": entry.state or "playing",
        # Full track payload (as in listens) so the profile can show the
        # playing track as the "now" row of its listening history.
        "track": track_summary_dict(store, track, artists),
    }


def people(store: Store, settings) -> list[dict[str, object]]:
    """Every user, the viewer included: playing-now first, then by last login (newest)."""
    entries = now_playing_entries(settings) or []
    playing = _freshest_by_username(entries)
    rows = list(store.list_users())
    rows.sort(key=lambda row: str(row["last_login_at"] or ""), reverse=True)
    items: list[dict[str, object]] = []
    for row in rows:
        username = str(row["navidrome_username"])
        entry = playing.get(username.casefold())
        items.append(
            {
                "username": username,
                "avatar": ensure_user_avatar(store, int(row["id"])),
                "now_playing": _now_playing_dict(store, entry) if entry is not None else None,
            }
        )
    # Stable: keeps the last-login order inside both groups.
    items.sort(key=lambda item: item["now_playing"] is None)
    return items
