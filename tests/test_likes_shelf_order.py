"""Favourite shelves are ordered by when something was liked.

They used to order by `updated_at`, which every playback event bumps — so the
shelf was really sorted by "last played", and playing an old favourite pushed
it above one liked yesterday. See plans/likes-unification-plan.md.
"""
from __future__ import annotations

from pathlib import Path

from app.models import PlaybackEventCreate, utc_now
from app.scanner import ScannedTrack
from app.services.dashboard import _dashboard_liked_artists, _dashboard_liked_releases
from app.store import Store


def _store_with_two_artists(tmp_path: Path) -> tuple[Store, list[int], list[int]]:
    root = Store(tmp_path / "app.db")
    root.init()
    user_id = root.upsert_user("alice", now=utc_now())
    store = root.for_user(user_id)
    artist_ids: list[int] = []
    track_ids: list[int] = []
    for name in ("First Artist", "Second Artist"):
        track_id, _ = root.upsert_track(
            ScannedTrack(
                path=(tmp_path / f"{name}.flac").resolve(),
                artist=name,
                title=f"{name} Track",
                album=f"{name} Album",
                duration=120.0,
                file_size=1,
                mtime=1,
            )
        )
        track_ids.append(track_id)
        artist_ids.append(store.artist_ids_for_track(track_id)[0])
    return store, track_ids, artist_ids


def _set_liked_at(store: Store, artist_id: int, stamp: str) -> None:
    with store.connect() as conn:
        conn.execute(
            "UPDATE user_artist_preferences SET liked_at = ? "
            "WHERE user_id = discocs_user_id() AND artist_id = ?",
            (stamp, artist_id),
        )


def test_liked_artists_shelf_puts_the_newest_like_first(tmp_path: Path):
    store, _track_ids, artist_ids = _store_with_two_artists(tmp_path)
    older, newer = artist_ids
    store.set_artist_liked(older, True)
    store.set_artist_liked(newer, True)
    _set_liked_at(store, older, "2020-01-01T00:00:00+00:00")
    _set_liked_at(store, newer, "2026-01-01T00:00:00+00:00")

    items, total = _dashboard_liked_artists(store, 10, 0)

    assert total == 2
    assert [item["entity_id"] for item in items] == [newer, older]


def test_playing_an_old_favourite_does_not_reorder_the_shelf(tmp_path: Path):
    """The exact regression `updated_at` ordering caused."""
    store, track_ids, artist_ids = _store_with_two_artists(tmp_path)
    older, newer = artist_ids
    store.set_artist_liked(older, True)
    store.set_artist_liked(newer, True)
    _set_liked_at(store, older, "2020-01-01T00:00:00+00:00")
    _set_liked_at(store, newer, "2026-01-01T00:00:00+00:00")

    # Listening to the older favourite bumps its updated_at, not its liked_at.
    store.record_playback_event(
        PlaybackEventCreate(event_type="play_threshold_reached", track_id=track_ids[0])
    )

    items, _total = _dashboard_liked_artists(store, 10, 0)
    assert [item["entity_id"] for item in items] == [newer, older]


def test_shelf_falls_back_to_updated_at_when_liked_at_is_missing(tmp_path: Path):
    """Rows liked before the column existed must still appear, not vanish."""
    store, _track_ids, artist_ids = _store_with_two_artists(tmp_path)
    artist_id = artist_ids[0]
    store.set_artist_liked(artist_id, True)
    with store.connect() as conn:
        conn.execute(
            "UPDATE user_artist_preferences SET liked_at = NULL "
            "WHERE user_id = discocs_user_id() AND artist_id = ?",
            (artist_id,),
        )

    items, total = _dashboard_liked_artists(store, 10, 0)

    assert total == 1
    assert [item["entity_id"] for item in items] == [artist_id]


def test_liked_releases_shelf_is_ordered_the_same_way(tmp_path: Path):
    store, track_ids, _artist_ids = _store_with_two_artists(tmp_path)
    release_ids = []
    with store.connect() as conn:
        for track_id in track_ids:
            release_ids.append(
                int(
                    conn.execute(
                        "SELECT release_id FROM release_tracks WHERE track_id = ?",
                        (track_id,),
                    ).fetchone()["release_id"]
                )
            )
    older, newer = release_ids
    store.set_release_liked(older, True)
    store.set_release_liked(newer, True)
    with store.connect() as conn:
        conn.execute(
            "UPDATE user_release_preferences SET liked_at = ? "
            "WHERE user_id = discocs_user_id() AND release_id = ?",
            ("2020-01-01T00:00:00+00:00", older),
        )
        conn.execute(
            "UPDATE user_release_preferences SET liked_at = ? "
            "WHERE user_id = discocs_user_id() AND release_id = ?",
            ("2026-01-01T00:00:00+00:00", newer),
        )

    items, total = _dashboard_liked_releases(store, 10, 0)

    assert total == 2
    assert [item["entity_id"] for item in items] == [newer, older]
