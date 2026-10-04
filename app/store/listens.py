"""Store Listens domain: materialized listening history (social features, Ф1).

Part of the app/store package. Do not import this module directly; use
app.store instead.

A *listen* is exactly what discocs scrobbles to Navidrome: the decision is the
single predicate ``playback_event_is_listen`` (app/store/_helpers.py). It is
taken once, inside the transaction that records the playback event
(``PlaybackStoreMixin.record_playback_event``), so a listen exists whether or
not Navidrome is configured or the scrobble call succeeds. ``listens`` is a
derived table: ``backfill_listens_from_events`` rebuilds it from
``playback_events`` with the same predicate. See docs/social.md and
plans/social-spec.md §2.1.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from app.models import Listen, PlaybackEvent
from app.store._helpers import (
    LISTEN_EVENT_TYPES,
    playback_event_is_listen,
    row_to_playback_event,
)

_LISTEN_TYPES = tuple(sorted(LISTEN_EVENT_TYPES))
_LISTEN_TYPES_PLACEHOLDERS = ", ".join("?" for _type in _LISTEN_TYPES)

# event_id is UNIQUE: re-deciding the same event (backfill rerun, retried
# transaction) never produces a second listen.
_INSERT_LISTEN = (
    "INSERT OR IGNORE INTO listens (user_id, track_id, listened_at, event_id) "
    "VALUES (?, ?, ?, ?)"
)


def _row_to_listen(row: sqlite3.Row) -> Listen:
    return Listen(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        track_id=int(row["track_id"]),
        listened_at=str(row["listened_at"]),
        event_id=str(row["event_id"]),
    )


def _prior_session_events(
    conn: sqlite3.Connection,
    user_id: int,
    event: PlaybackEvent,
) -> Iterator[PlaybackEvent]:
    """Earlier listen-type events of the event's session, queried lazily.

    A generator: the query only runs if the predicate actually needs the
    session history (a qualifying ``completed`` inside a session).
    """
    if not event.session_id:
        return
    rows = conn.execute(
        f"""
        SELECT * FROM playback_events
        WHERE user_id = ? AND session_id = ? AND id != ?
          AND event_type IN ({_LISTEN_TYPES_PLACEHOLDERS})
        ORDER BY rowid
        """,
        (user_id, event.session_id, event.id, *_LISTEN_TYPES),
    ).fetchall()
    for row in rows:
        yield row_to_playback_event(row)


def record_listen_for_event(
    conn: sqlite3.Connection,
    user_id: int,
    event: PlaybackEvent,
) -> bool:
    """Judge a freshly recorded (non-duplicate) event and store its listen.

    Must run on the connection that just inserted ``event`` so the decision
    and the event commit together. Returns the decision.
    """
    if not playback_event_is_listen(event, _prior_session_events(conn, user_id, event)):
        return False
    conn.execute(_INSERT_LISTEN, (user_id, event.track_id, event.created_at, event.id))
    return True


def backfill_listens_from_events(conn: sqlite3.Connection) -> int:
    """Derive ``listens`` from the stored ``playback_events`` of every user.

    Replays each session's listen-type events in insertion order (``rowid`` —
    exactly the history the live decision saw) through the same predicate.
    Events without a ``user_id`` (or with an unknown one) are skipped.
    Idempotent: existing listens are kept via ``INSERT OR IGNORE`` on
    ``event_id``. Returns the number of listens inserted.
    """
    rows = conn.execute(
        f"""
        SELECT e.* FROM playback_events e
        JOIN users u ON u.id = e.user_id
        WHERE e.event_type IN ({_LISTEN_TYPES_PLACEHOLDERS})
        ORDER BY e.rowid
        """,
        _LISTEN_TYPES,
    ).fetchall()
    priors_by_session: dict[tuple[int, str], list[PlaybackEvent]] = {}
    pending: list[tuple[int, int | None, str, str]] = []
    for row in rows:
        user_id = int(row["user_id"])
        event = row_to_playback_event(row)
        priors: list[PlaybackEvent] = []
        if event.session_id:
            priors = priors_by_session.setdefault((user_id, event.session_id), [])
        if playback_event_is_listen(event, priors):
            pending.append((user_id, event.track_id, event.created_at, event.id))
        if event.session_id:
            priors.append(event)
    before = conn.total_changes
    conn.executemany(_INSERT_LISTEN, pending)
    return conn.total_changes - before


class ListensStoreMixin:
    def list_listens(self, *, limit: int = 50, offset: int = 0) -> list[Listen]:
        """The current user's listens, newest first (repeats included)."""
        self.require_user_id()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM listens
                WHERE user_id = discocs_user_id()
                ORDER BY listened_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (int(limit), int(offset)),
            ).fetchall()
        return [_row_to_listen(row) for row in rows]

    def count_listens(self) -> int:
        self.require_user_id()
        with self.connect() as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM listens WHERE user_id = discocs_user_id()"
                ).fetchone()[0]
            )

    def backfill_listens(self) -> int:
        """Re-run the global backfill (all users); safe to repeat."""
        with self.connect() as conn:
            return backfill_listens_from_events(conn)
