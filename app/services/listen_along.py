"""Listen along: pick up another user's playback once (social Ф6, plans/social-spec.md §1.6).

``POST /api/v1/users/{username}/listen-along`` gives the viewer their *own* new
playback session that starts where the host is right now: same track, same
position (corrected for the time since the host's last presence report), and
— for an ordinary source — the rest of the host's queue in play order. Nothing
is synchronised afterwards and nothing changes for the host.

Rules (contract: docs/social.md "Слушать вместе"):

* what the host plays comes from Navidrome ``getNowPlaying`` (the same cached
  call as the people shelf); not playing → ``ListenAlongUnavailableError``
  (``not_playing``), a song without a discocs track → ``track_not_mapped``;
* the host's session is read through a store bound to the host
  (``Store.for_user``) with exactly one explicit method,
  ``playback_presence_snapshot`` — the viewer's store stays default-deny;
* ordinary source (release/artist/label/playlist/search/manual/…): copy the
  remaining queue from the current item, in play order (shuffle included);
* personal source (flow, generated mix), no discocs session, or the track not
  found in the session's queue: "track radio" — a one-track queue, autoplay
  continues by similarity;
* the viewer's session: ``source_type = "listen_along"``, ``source_id`` = the
  host's user id, ``source_label`` = the host's username, autoplay on.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.models import PlaybackPresenceSnapshot, PlaybackSession, QueueItem, Track
from app.navidrome import NowPlayingEntry
from app.services.presence import now_playing_for
from app.services.profile import resolve_profile_target, viewer_user_id
from app.store import Store

LISTEN_ALONG_SOURCE_TYPE = "listen_along"
# The host's queue is generated from their personal taste/history: never copied.
PERSONAL_SOURCE_TYPES = frozenset({"flow", "generated_mix"})
# Presence states in which the position keeps moving after the report.
ADVANCING_PRESENCE_STATES = frozenset({"starting", "playing"})

STRATEGY_QUEUE = "queue"
STRATEGY_PERSONAL_SOURCE = "personal_source"
STRATEGY_NO_SESSION = "no_session"
STRATEGY_NOT_IN_QUEUE = "not_in_queue"


class ListenAlongSelfError(ValueError):
    """Listening along with yourself makes no sense (HTTP 400)."""


class ListenAlongUnavailableError(LookupError):
    """The host is not playing anything discocs can start (HTTP 409)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ListenAlongPlan:
    track_ids: list[int]
    mode: str
    position_ms: int
    strategy: str


@dataclass(frozen=True)
class ListenAlongStart:
    session: PlaybackSession
    queue: list[QueueItem]
    host_username: str
    start_track_id: int
    position_ms: int
    strategy: str


def _parse_utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _clip_to_duration(position_ms: float, duration_seconds: float | None) -> int:
    """A position at/after the end of the track is stale → start from 0."""
    position = max(0, int(position_ms))
    if duration_seconds and position >= duration_seconds * 1000:
        return 0
    return position


def live_position_ms(
    *,
    state: str | None,
    position_ms: int | None,
    reported_at: str | None,
    duration_seconds: float | None,
    now: datetime,
) -> int:
    """Where the host is now, from their last presence report.

    Playing/starting: the reported position plus the time since the report;
    paused/stopped: the reported position as is. No report → 0.
    """
    if position_ms is None or not reported_at:
        return 0
    reported = _parse_utc(reported_at)
    if reported is None:
        return 0
    position = float(position_ms)
    if state in ADVANCING_PRESENCE_STATES:
        position += max(0.0, (now - reported).total_seconds() * 1000)
    return _clip_to_duration(position, duration_seconds)


def remaining_queue_track_ids(snapshot: PlaybackPresenceSnapshot, track_id: int) -> list[int]:
    """Track ids from the host's current item to the end, in play order.

    ``snapshot.queue`` is already ordered by ``position`` — the play order,
    which shuffle rewrites — so slicing it keeps the host's shuffled order.
    The current item is the reported one, else the session's current item,
    else the first queue item with the playing track; it must hold
    ``track_id``. Not found → [].
    """
    items = snapshot.queue
    for candidate in (snapshot.presence_queue_item_id, snapshot.session.current_queue_item_id):
        if not candidate:
            continue
        for index, item in enumerate(items):
            if item.id == candidate and item.track_id == track_id:
                return [queued.track_id for queued in items[index:]]
    for index, item in enumerate(items):
        if item.track_id == track_id:
            return [queued.track_id for queued in items[index:]]
    return []


def _navidrome_position_ms(entry: NowPlayingEntry, track: Track) -> int:
    if entry.position_ms is None:
        return 0
    return _clip_to_duration(entry.position_ms, track.duration)


def plan_listen_along(
    snapshot: PlaybackPresenceSnapshot | None,
    track: Track,
    entry: NowPlayingEntry,
    *,
    now: datetime,
) -> ListenAlongPlan:
    radio = [track.id]
    if snapshot is None:
        return ListenAlongPlan(radio, "radio", _navidrome_position_ms(entry, track), STRATEGY_NO_SESSION)
    if snapshot.presence_track_id == track.id:
        position_ms = live_position_ms(
            state=snapshot.presence_state,
            position_ms=snapshot.presence_position_ms,
            reported_at=snapshot.presence_at,
            duration_seconds=track.duration,
            now=now,
        )
    else:
        # Matched by current_track_id only: the presence report (if any) is
        # about another track, so its position means nothing here.
        position_ms = _navidrome_position_ms(entry, track)
    if snapshot.session.source_type in PERSONAL_SOURCE_TYPES:
        return ListenAlongPlan(radio, "radio", position_ms, STRATEGY_PERSONAL_SOURCE)
    remaining = remaining_queue_track_ids(snapshot, track.id)
    if not remaining:
        return ListenAlongPlan(radio, "radio", position_ms, STRATEGY_NOT_IN_QUEUE)
    return ListenAlongPlan(remaining, "linear", position_ms, STRATEGY_QUEUE)


def listen_along(
    viewer_store: Store,
    settings,
    username: str,
    *,
    session_settings: dict[str, object] | None = None,
    now: datetime | None = None,
) -> ListenAlongStart:
    viewer_id = viewer_user_id(viewer_store)
    target = resolve_profile_target(viewer_store, username)
    if target.user_id == viewer_id:
        raise ListenAlongSelfError("Cannot listen along with yourself")
    entry = now_playing_for(settings, target.username)
    if entry is None:
        raise ListenAlongUnavailableError("not_playing", f"{target.username} is not playing anything")
    track = viewer_store.get_track_by_external_id("navidrome", entry.id)
    if track is None:
        raise ListenAlongUnavailableError(
            "track_not_mapped", f"The track {target.username} is playing is not in the library"
        )
    # The one cross-user read: the host-bound store, one explicit method.
    snapshot = target.store.playback_presence_snapshot(track.id)
    plan = plan_listen_along(snapshot, track, entry, now=now or datetime.now(UTC))
    session, queue = viewer_store.create_playback_session(
        source_type=LISTEN_ALONG_SOURCE_TYPE,
        source_id=target.user_id,
        source_label=target.username,
        mode=plan.mode,
        track_ids=plan.track_ids,
        autoplay_enabled=True,
        shuffle_enabled=False,
        settings=session_settings,
    )
    return ListenAlongStart(
        session=session,
        queue=queue,
        host_username=target.username,
        start_track_id=track.id,
        position_ms=plan.position_ms,
        strategy=plan.strategy,
    )
