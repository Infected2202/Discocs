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

The mixin also answers the profile statistics (Ф3): period summaries, local-time
buckets, top artists/releases/tracks and listen-weighted prediction labels,
each scoped to the bound user via ``discocs_user_id()``.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from app.models import Listen, ListenCount, ListenSummary, PlaybackEvent, Track
from app.store._helpers import (
    LISTEN_EVENT_TYPES,
    playback_event_is_listen,
    row_to_playback_event,
    row_to_track,
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


# Profile statistics (Ф3) only count listens whose track is still in the
# library (``tracks`` row exists; ``listens.track_id`` has no FK), so every
# number matches what the profile can actually show. Alias ``l`` = listens,
# ``t`` = tracks; the bound user is ``discocs_user_id()``.
_LIBRARY_LISTENS = """
    FROM listens l
    JOIN tracks t ON t.id = l.track_id
"""


def _window(since: str | None, until: str | None) -> tuple[str, tuple[str, ...]]:
    """WHERE clause for the bound user's listens in ``[since, until]``.

    Bounds are UTC ISO-8601 strings in the same format as ``listened_at``
    (``datetime.isoformat()`` of an aware UTC datetime), so plain string
    comparison is chronological and the ``(user_id, listened_at)`` index applies.
    ``None`` leaves that side open.
    """
    clauses = ["l.user_id = discocs_user_id()"]
    params: list[str] = []
    if since is not None:
        clauses.append("l.listened_at >= ?")
        params.append(since)
    if until is not None:
        clauses.append("l.listened_at <= ?")
        params.append(until)
    return "WHERE " + " AND ".join(clauses), tuple(params)


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

    # ------------------------------------------------------------------
    # Profile statistics (social Ф3). Read on a store bound to the profile
    # owner (app/services/profile.py); listens of purged tracks are ignored.
    # ------------------------------------------------------------------

    def count_library_listens(self) -> int:
        self.require_user_id()
        where, params = _window(None, None)
        with self.connect() as conn:
            return int(conn.execute(f"SELECT COUNT(*) {_LIBRARY_LISTENS} {where}", params).fetchone()[0])

    def list_library_listens(self, *, limit: int = 50, offset: int = 0) -> list[tuple[Listen, Track]]:
        """Listens with their tracks, newest first; repeats included."""
        self.require_user_id()
        where, params = _window(None, None)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT t.*, l.id AS listen_id, l.user_id AS listen_user_id,
                       l.listened_at, l.event_id AS listen_event_id
                {_LIBRARY_LISTENS}
                {where}
                ORDER BY l.listened_at DESC, l.id DESC
                LIMIT ? OFFSET ?
                """,
                (*params, int(limit), int(offset)),
            ).fetchall()
        return [
            (
                Listen(
                    id=int(row["listen_id"]),
                    user_id=int(row["listen_user_id"]),
                    track_id=int(row["id"]),
                    listened_at=str(row["listened_at"]),
                    event_id=str(row["listen_event_id"]),
                ),
                row_to_track(row),
            )
            for row in rows
        ]

    def listen_summary(self, *, since: str | None = None, until: str | None = None) -> ListenSummary:
        """Listens, listened seconds (sum of track durations) and distinct
        credited artists in the window — all in SQL, also for all time."""
        self.require_user_id()
        where, params = _window(since, until)
        with self.connect() as conn:
            totals = conn.execute(
                f"""
                SELECT COUNT(*) AS listens, COALESCE(SUM(COALESCE(t.duration, 0)), 0) AS seconds
                {_LIBRARY_LISTENS}
                {where}
                """,
                params,
            ).fetchone()
            artists = conn.execute(
                f"""
                SELECT COUNT(DISTINCT ta.artist_id)
                {_LIBRARY_LISTENS}
                JOIN track_artists ta ON ta.track_id = l.track_id
                {where}
                """,
                params,
            ).fetchone()[0]
        return ListenSummary(
            listens=int(totals["listens"]),
            seconds=float(totals["seconds"]),
            artists=int(artists or 0),
        )

    def listen_times(self, *, since: str | None = None, until: str | None = None) -> list[str]:
        """``listened_at`` of every listen in the window (for local-time buckets)."""
        self.require_user_id()
        where, params = _window(since, until)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT l.listened_at {_LIBRARY_LISTENS} {where} ORDER BY l.listened_at",
                params,
            ).fetchall()
        return [str(row["listened_at"]) for row in rows]

    # Ties in every top list: more listens first, then the most recently
    # listened, then the lowest id — deterministic for equal counts.

    def top_listened_artists(
        self, *, since: str | None = None, until: str | None = None, limit: int = 12
    ) -> list[ListenCount]:
        """Artists by listens; a multi-artist track counts for every credited
        artist (once per listen, whatever the number of credit roles)."""
        self.require_user_id()
        where, params = _window(since, until)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT a.id, a.name, COUNT(DISTINCT l.id) AS listens, MAX(l.listened_at) AS last_listened_at
                {_LIBRARY_LISTENS}
                JOIN track_artists ta ON ta.track_id = l.track_id
                JOIN artists a ON a.id = ta.artist_id
                {where}
                GROUP BY a.id
                ORDER BY listens DESC, last_listened_at DESC, a.id ASC
                LIMIT ?
                """,
                (*params, int(limit)),
            ).fetchall()
        return [ListenCount(int(row["id"]), int(row["listens"]), str(row["name"])) for row in rows]

    def top_listened_releases(
        self, *, since: str | None = None, until: str | None = None, limit: int = 12
    ) -> list[ListenCount]:
        """Releases by listens. A track on several releases counts for the one
        its track payload shows (lowest ``release_tracks.position``)."""
        self.require_user_id()
        where, params = _window(since, until)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT r.id, COUNT(*) AS listens, MAX(l.listened_at) AS last_listened_at
                {_LIBRARY_LISTENS}
                JOIN releases r ON r.id = (
                    SELECT rt.release_id FROM release_tracks rt
                    WHERE rt.track_id = l.track_id
                    ORDER BY rt.position, rt.release_id
                    LIMIT 1
                )
                {where}
                GROUP BY r.id
                ORDER BY listens DESC, last_listened_at DESC, r.id ASC
                LIMIT ?
                """,
                (*params, int(limit)),
            ).fetchall()
        return [ListenCount(int(row["id"]), int(row["listens"])) for row in rows]

    def top_listened_tracks(
        self, *, since: str | None = None, until: str | None = None, limit: int = 20
    ) -> list[ListenCount]:
        self.require_user_id()
        where, params = _window(since, until)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT l.track_id, COUNT(*) AS listens, MAX(l.listened_at) AS last_listened_at
                {_LIBRARY_LISTENS}
                {where}
                GROUP BY l.track_id
                ORDER BY listens DESC, last_listened_at DESC, l.track_id ASC
                LIMIT ?
                """,
                (*params, int(limit)),
            ).fetchall()
        return [ListenCount(int(row["track_id"]), int(row["listens"])) for row in rows]

    def listened_prediction_labels(
        self,
        model_name: str,
        *,
        since: str | None = None,
        until: str | None = None,
        limit: int = 8,
    ) -> tuple[list[ListenCount], int]:
        """Rank-1 labels of a head model, weighted by listens.

        Every listen of a track adds one to that track's top label. Returns
        the top labels and the number of listens whose track has a prediction
        for the model (the denominator for shares).
        """
        self.require_user_id()
        where, params = _window(since, until)
        join = (
            "JOIN track_predictions p ON p.track_id = l.track_id "
            "AND p.model_name = ? AND p.rank = 1"
        )
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT p.label, COUNT(*) AS listens
                {_LIBRARY_LISTENS}
                {join}
                {where}
                GROUP BY p.label
                ORDER BY listens DESC, p.label ASC
                LIMIT ?
                """,
                (model_name, *params, int(limit)),
            ).fetchall()
            total = conn.execute(
                f"SELECT COUNT(*) {_LIBRARY_LISTENS} {join} {where}",
                (model_name, *params),
            ).fetchone()[0]
        return [ListenCount(str(row["label"]), int(row["listens"])) for row in rows], int(total or 0)
