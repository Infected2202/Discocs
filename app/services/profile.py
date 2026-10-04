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

Ф3 adds the payload builders behind ``/api/v1/users/{username}/…``
(app/api/profile.py): statistics for a period in the viewer's timezone, the
listening history, likes and playlists. The response contract is documented
in docs/social.md.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.avatars import ensure_user_avatar
from app.models import Listen, Playlist, Track
from app.serializers.entities import track_summary_dict
from app.serializers.playlists import playlist_summary_dict
from app.serializers.search import _release_shelf_item, _track_shelf_item, artist_shelf_item
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


# Period switch of the profile page: key → number of local calendar days
# (today included); ``all`` = since the first listen.
PROFILE_PERIODS: dict[str, int | None] = {
    "7d": 7,
    "30d": 30,
    "90d": 90,
    "180d": 180,
    "365d": 365,
    "all": None,
}
DEFAULT_PROFILE_PERIOD = "30d"
# Longer spans (only possible for ``all``) are bucketed by calendar month.
MAX_DAY_BUCKETS = 366
TOP_ARTISTS_LIMIT = 12
TOP_RELEASES_LIMIT = 12
TOP_TRACKS_LIMIT = 5
RECENT_LISTENS_LIMIT = 10
SOUND_LABELS_LIMIT = 8
# Sound profile: rank-1 label of these Discogs-EffNet heads per listened track.
GENRE_MODEL = "genre_discogs400"
MOOD_MODEL = "mtg_jamendo_moodtheme"
_GENRE_SEPARATOR = "---"
_MAX_TZ_NAME_LENGTH = 64


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


def _header(viewer_store: Store, viewer_id: int, target: ProfileTarget) -> dict[str, object]:
    return {
        "username": target.username,
        "avatar": ensure_user_avatar(viewer_store, target.user_id),
        "created_at": target.created_at,
        "viewer_is_owner": viewer_id == target.user_id,
    }


def profile_header(viewer_store: Store, username: str) -> dict[str, object]:
    """Whitelisted header fields of a profile (§1.3 «Шапка»)."""
    viewer_id = viewer_user_id(viewer_store)
    return _header(viewer_store, viewer_id, resolve_profile_target(viewer_store, username))


def _owned_playlists(viewer_id: int, target: ProfileTarget) -> list[Playlist]:
    return target.store.list_owned_playlists(
        include_private=can_view_private_playlists(viewer_id, target.user_id),
    )


def profile_playlists(viewer_store: Store, username: str) -> list[Playlist]:
    """The target's own playlists; private ones only on their own profile."""
    viewer_id = viewer_user_id(viewer_store)
    return _owned_playlists(viewer_id, resolve_profile_target(viewer_store, username))


# ---------------------------------------------------------------------------
# Time window in the viewer's timezone (§2.5)
# ---------------------------------------------------------------------------

def resolve_timezone(name: str | None) -> tuple[str, tzinfo]:
    """IANA name → zone; missing or invalid names fall back to UTC."""
    clean = (name or "").strip()
    if clean and len(clean) <= _MAX_TZ_NAME_LENGTH:
        try:
            return clean, ZoneInfo(clean)
        except (ZoneInfoNotFoundError, ValueError, OSError, UnicodeError):
            pass
    return "UTC", UTC


def _utc_iso(value: datetime) -> str:
    """Same format as ``listened_at`` (``utc_now()``), so strings compare in time order."""
    return value.astimezone(UTC).isoformat()


def _parse_listened_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class ProfileWindow:
    period: str
    tz_name: str
    tz: tzinfo
    # Local calendar days covered (inclusive); ``start_date`` is None for an
    # ``all`` window of a user without listens.
    start_date: date | None
    end_date: date
    # UTC bounds for SQL; ``since`` None = open (``all``).
    since: str | None
    until: str


def profile_window(period: str, tz_name: str | None, now: datetime | None = None) -> ProfileWindow:
    """The last N local calendar days including today, up to ``now``.

    A 7d window viewed at 10:00 Europe/Moscow on Oct 4 starts at Sep 28 00:00
    Moscow time (Sep 27 21:00 UTC), so per-day bars and period totals agree.
    """
    if period not in PROFILE_PERIODS:
        raise ValueError(f"Unknown profile period: {period}")
    name, zone = resolve_timezone(tz_name)
    current = (now or datetime.now(UTC)).astimezone(UTC)
    end_date = current.astimezone(zone).date()
    days = PROFILE_PERIODS[period]
    if days is None:
        return ProfileWindow(period, name, zone, None, end_date, None, _utc_iso(current))
    start_date = end_date - timedelta(days=days - 1)
    local_midnight = datetime.combine(start_date, time.min, tzinfo=zone)
    return ProfileWindow(
        period, name, zone, start_date, end_date, _utc_iso(local_midnight), _utc_iso(current)
    )


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _next_month(day: date) -> date:
    return date(day.year + 1, 1, 1) if day.month == 12 else date(day.year, day.month + 1, 1)


def _bucket_keys(start: date, end: date, bucket: str) -> list[date]:
    keys: list[date] = []
    if bucket == "day":
        current = start
        while current <= end:
            keys.append(current)
            current += timedelta(days=1)
        return keys
    current = _month_start(start)
    while current <= end:
        keys.append(current)
        current = _next_month(current)
    return keys


def bucket_listens(
    listened_at: list[str],
    zone: tzinfo,
    start_date: date | None,
    end_date: date,
) -> tuple[str, list[dict[str, object]], list[int]]:
    """Per-day (or per-month) and per-hour counts in the viewer's timezone.

    Every bucket of the window is present, zeros included. Spans longer than
    ``MAX_DAY_BUCKETS`` days are bucketed by month (``date`` = first day of
    the month). Returns ``(bucket, by_day, by_hour)``.
    """
    local = [_parse_listened_at(value).astimezone(zone) for value in listened_at]
    by_hour = [0] * 24
    for moment in local:
        by_hour[moment.hour] += 1
    if start_date is None:
        if not local:
            return "day", [], by_hour
        start_date = min(moment.date() for moment in local)
    span_days = (end_date - start_date).days + 1
    bucket = "day" if span_days <= MAX_DAY_BUCKETS else "month"
    counts: Counter[date] = Counter(
        moment.date() if bucket == "day" else _month_start(moment.date()) for moment in local
    )
    by_day = [
        {"date": key.isoformat(), "listens": counts.get(key, 0)}
        for key in _bucket_keys(start_date, end_date, bucket)
    ]
    return bucket, by_day, by_hour


# ---------------------------------------------------------------------------
# Payload pieces (whitelisted shapes shared with the dashboard/track lists)
# ---------------------------------------------------------------------------

def _track_payloads(store: Store, tracks: list[Track]) -> list[dict[str, object]]:
    artists_by_track = store.artists_for_tracks([track.id for track in tracks])
    return [track_summary_dict(store, track, artists_by_track.get(track.id, [])) for track in tracks]


def _listen_items(store: Store, rows: list[tuple[Listen, Track]]) -> list[dict[str, object]]:
    payloads = _track_payloads(store, [track for _listen, track in rows])
    items: list[dict[str, object]] = []
    for (listen, _track), payload in zip(rows, payloads, strict=True):
        payload["listen_id"] = listen.id
        payload["listened_at"] = _utc_iso(_parse_listened_at(listen.listened_at))
        items.append(payload)
    return items


def _sound_labels(store: Store, model_name: str, window: ProfileWindow) -> list[dict[str, object]]:
    counts, total = store.listened_prediction_labels(
        model_name, since=window.since, until=window.until, limit=SOUND_LABELS_LIMIT
    )
    return [
        {"label": str(count.key), "listens": count.listens, "share": round(count.listens / total, 4)}
        for count in counts
        if total
    ]


def _genre_items(store: Store, window: ProfileWindow) -> list[dict[str, object]]:
    items = _sound_labels(store, GENRE_MODEL, window)
    for item in items:
        genre, _sep, style = str(item["label"]).partition(_GENRE_SEPARATOR)
        item["genre"] = genre
        item["style"] = style or None
    return items


def _top_artists(store: Store, window: ProfileWindow) -> list[dict[str, object]]:
    items = []
    for count in store.top_listened_artists(since=window.since, until=window.until, limit=TOP_ARTISTS_LIMIT):
        item = artist_shelf_item(int(count.key), str(count.name))
        item["listens"] = count.listens
        items.append(item)
    return items


def _top_releases(store: Store, window: ProfileWindow) -> list[dict[str, object]]:
    items = []
    for count in store.top_listened_releases(since=window.since, until=window.until, limit=TOP_RELEASES_LIMIT):
        release = store.get_release(int(count.key))
        if release is None:
            continue
        item = _release_shelf_item(release)
        item["listens"] = count.listens
        items.append(item)
    return items


def _top_tracks(store: Store, window: ProfileWindow) -> list[dict[str, object]]:
    counts = store.top_listened_tracks(since=window.since, until=window.until, limit=TOP_TRACKS_LIMIT)
    tracks_by_id = store.get_tracks([int(count.key) for count in counts])
    present = [(count, tracks_by_id[int(count.key)]) for count in counts if int(count.key) in tracks_by_id]
    payloads = _track_payloads(store, [track for _count, track in present])
    for (count, _track), payload in zip(present, payloads, strict=True):
        payload["listens"] = count.listens
    return payloads


# ---------------------------------------------------------------------------
# Endpoint payloads
# ---------------------------------------------------------------------------

def profile_stats(
    viewer_store: Store,
    username: str,
    *,
    period: str = DEFAULT_PROFILE_PERIOD,
    tz_name: str | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """``GET /users/{username}/profile`` — header, period stats, tops, recent."""
    viewer_id = viewer_user_id(viewer_store)
    target = resolve_profile_target(viewer_store, username)
    store = target.store
    window = profile_window(period, tz_name, now)

    all_time = store.listen_summary()
    header = _header(viewer_store, viewer_id, target)
    header["totals"] = {
        "listens": all_time.listens,
        "artists": all_time.artists,
        "likes": store.count_liked_tracks(),
    }

    summary = store.listen_summary(since=window.since, until=window.until)
    # ``all``: start_date is None, so the buckets start at the first listen.
    bucket, by_day, by_hour = bucket_listens(
        store.listen_times(since=window.since, until=window.until),
        window.tz,
        window.start_date,
        window.end_date,
    )
    return {
        "header": header,
        "period": {
            "key": window.period,
            "tz": window.tz_name,
            "since": window.since,
            "until": window.until,
        },
        "summary": {
            "listens": summary.listens,
            "hours": round(summary.seconds / 3600, 1),
            "artists": summary.artists,
        },
        "by_day_bucket": bucket,
        "by_day": by_day,
        "by_hour": by_hour,
        "sound": {
            "genres": _genre_items(store, window),
            "moods": _sound_labels(store, MOOD_MODEL, window),
        },
        "top_artists": _top_artists(store, window),
        "top_releases": _top_releases(store, window),
        "top_tracks": _top_tracks(store, window),
        "recent": _listen_items(store, store.list_library_listens(limit=RECENT_LISTENS_LIMIT)),
    }


def profile_listens(viewer_store: Store, username: str, *, limit: int, offset: int) -> dict[str, object]:
    """``GET /users/{username}/listens`` — the full history, newest first."""
    viewer_user_id(viewer_store)
    store = resolve_profile_target(viewer_store, username).store
    total = store.count_library_listens()
    return {
        "items": _listen_items(store, store.list_library_listens(limit=limit, offset=offset)),
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": offset + limit if offset + limit < total else None,
    }


def profile_likes(viewer_store: Store, username: str, *, limit: int, offset: int = 0) -> dict[str, object]:
    """``GET /users/{username}/likes`` — liked tracks, releases and artists as shelf items."""
    from app.services.dashboard import (  # noqa: PLC0415 — heavy module, profile-only use
        _dashboard_liked_artists,
        _dashboard_liked_releases,
    )

    viewer_user_id(viewer_store)
    store = resolve_profile_target(viewer_store, username).store
    tracks = store.list_liked_tracks(limit=limit, offset=offset)
    artists_by_track = store.artists_for_tracks([track.id for track in tracks])
    releases, releases_total = _dashboard_liked_releases(store, limit, offset)
    artists, artists_total = _dashboard_liked_artists(store, limit, offset)
    return {
        "tracks": {
            "items": [
                _track_shelf_item(store, track, artists_by_track.get(track.id, []))
                for track in tracks
            ],
            "total": store.count_liked_tracks(),
        },
        "releases": {"items": releases, "total": releases_total},
        "artists": {"items": artists, "total": artists_total},
        "limit": limit,
        "offset": offset,
    }


def profile_playlists_payload(viewer_store: Store, username: str) -> dict[str, object]:
    """``GET /users/{username}/playlists`` — private ones only for the owner."""
    viewer_id = viewer_user_id(viewer_store)
    target = resolve_profile_target(viewer_store, username)
    playlists = _owned_playlists(viewer_id, target)
    counts = target.store.playlist_track_counts([playlist.id for playlist in playlists])
    items = []
    for playlist in playlists:
        item = playlist_summary_dict(target.store, playlist, counts.get(playlist.id, 0))
        # Serialized on the owner's store; editability is the viewer's.
        item["editable"] = viewer_id == target.user_id
        items.append(item)
    return {"items": items, "total": len(items)}
