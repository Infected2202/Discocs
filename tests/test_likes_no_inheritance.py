"""Phase 2 of the likes unification: `liked` never leaks between entities.

A track's star says nothing about its release or its artists. Before this, both
the play-state import and the preference-event appliers propagated `liked`
upwards, which is what put never-liked artists on the favourites shelf.
See plans/likes-unification-plan.md.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.models import PlaybackEventCreate, utc_now
from app.scanner import ScannedTrack
from app.store import Store


def _store_with_track(tmp_path: Path) -> tuple[Store, int, int, int]:
    """Return (store, track_id, release_id, artist_id) for one scanned track."""
    root = Store(tmp_path / "app.db")
    root.init()
    user_id = root.upsert_user("alice", now=utc_now())
    track_id, _ = root.upsert_track(
        ScannedTrack(
            path=(tmp_path / "track.flac").resolve(),
            artist="Some Artist",
            title="Some Track",
            album="Some Album",
            duration=120.0,
            file_size=1,
            mtime=1,
        )
    )
    store = root.for_user(user_id)
    artist_id = store.artist_ids_for_track(track_id)[0]
    with store.connect() as conn:
        release_id = conn.execute(
            "SELECT release_id FROM release_tracks WHERE track_id = ?", (track_id,)
        ).fetchone()["release_id"]
    return store, track_id, int(release_id), artist_id


def _liked(store: Store, track_id: int, release_id: int, artist_id: int) -> tuple[bool, bool, bool]:
    track = store.get_track_preference(track_id)
    release = store.get_release_preference(release_id)
    artist = store.get_artist_preference(artist_id)
    return (
        bool(track and track.liked),
        bool(release and release.liked),
        bool(artist and artist.liked),
    )


def test_liking_a_track_does_not_like_its_release_or_artist(tmp_path: Path):
    store, track_id, release_id, artist_id = _store_with_track(tmp_path)

    store.set_track_liked(track_id, True)

    assert _liked(store, track_id, release_id, artist_id) == (True, False, False)


def test_play_state_import_cannot_set_any_like(tmp_path: Path):
    """The play-state import was the main polluter; it no longer touches likes."""
    store, track_id, release_id, artist_id = _store_with_track(tmp_path)

    store.import_external_track_play_state(track_id, play_count=4)

    assert _liked(store, track_id, release_id, artist_id) == (False, False, False)


def test_play_state_import_rejects_a_liked_argument(tmp_path: Path):
    """Guard the invariant structurally: there is no way to pass a like in."""
    store, track_id, _release_id, _artist_id = _store_with_track(tmp_path)

    with pytest.raises(TypeError):
        store.import_external_track_play_state(track_id, liked=True)


def test_play_state_import_still_rolls_up_behavioural_counters(tmp_path: Path):
    """Only `liked` stopped propagating — play counts must still aggregate."""
    store, track_id, release_id, artist_id = _store_with_track(tmp_path)

    store.import_external_track_play_state(track_id, play_count=4)

    assert store.get_release_preference(release_id).play_count == 4
    assert store.get_artist_preference(artist_id).play_count == 4


def test_liked_playback_event_does_not_like_release_or_artist(tmp_path: Path):
    store, track_id, release_id, artist_id = _store_with_track(tmp_path)

    store.record_playback_event(
        PlaybackEventCreate(event_type="liked", track_id=track_id)
    )

    _, release_liked, artist_liked = _liked(store, track_id, release_id, artist_id)
    assert (release_liked, artist_liked) == (False, False)


def test_liked_playback_event_still_scores_release_and_artist(tmp_path: Path):
    store, track_id, release_id, artist_id = _store_with_track(tmp_path)

    store.record_playback_event(
        PlaybackEventCreate(event_type="liked", track_id=track_id)
    )

    assert store.get_release_preference(release_id).score > 0
    assert store.get_artist_preference(artist_id).score > 0


def test_setting_entity_likes_records_liked_at(tmp_path: Path):
    store, track_id, release_id, artist_id = _store_with_track(tmp_path)

    store.set_release_liked(release_id, True)
    store.set_artist_liked(artist_id, True)

    with store.connect() as conn:
        release_at = conn.execute(
            "SELECT liked_at FROM user_release_preferences WHERE release_id = ?",
            (release_id,),
        ).fetchone()["liked_at"]
        artist_at = conn.execute(
            "SELECT liked_at FROM user_artist_preferences WHERE artist_id = ?",
            (artist_id,),
        ).fetchone()["liked_at"]
    assert release_at is not None
    assert artist_at is not None


def test_unliking_clears_liked_at_but_keeps_score(tmp_path: Path):
    store, _track_id, _release_id, artist_id = _store_with_track(tmp_path)
    store.set_artist_liked(artist_id, True)
    scored = store.get_artist_preference(artist_id).score

    store.set_artist_liked(artist_id, False)

    pref = store.get_artist_preference(artist_id)
    assert pref.liked is False
    assert pref.score == scored
    with store.connect() as conn:
        liked_at = conn.execute(
            "SELECT liked_at FROM user_artist_preferences WHERE artist_id = ?",
            (artist_id,),
        ).fetchone()["liked_at"]
    assert liked_at is None


def test_sync_replaces_all_three_entity_types(tmp_path: Path):
    store, track_id, release_id, artist_id = _store_with_track(tmp_path)
    store.set_track_liked(track_id, True)
    store.set_release_liked(release_id, True)
    store.set_artist_liked(artist_id, True)

    # Navidrome now only reports the artist as starred.
    store.sync_likes_from_navidrome(
        track_ids=[], release_ids=[], artist_ids=[artist_id]
    )

    assert _liked(store, track_id, release_id, artist_id) == (False, False, True)


def test_sync_is_idempotent(tmp_path: Path):
    store, track_id, release_id, artist_id = _store_with_track(tmp_path)

    for _ in range(2):
        store.sync_likes_from_navidrome(
            track_ids=[track_id], release_ids=[release_id], artist_ids=[artist_id]
        )

    assert _liked(store, track_id, release_id, artist_id) == (True, True, True)


def test_sync_deduplicates_repeated_ids(tmp_path: Path):
    store, track_id, release_id, artist_id = _store_with_track(tmp_path)

    store.sync_likes_from_navidrome(
        track_ids=[track_id, track_id],
        release_ids=[release_id],
        artist_ids=[artist_id],
    )

    assert store.get_track_preference(track_id).liked is True


def test_liked_playback_event_does_not_set_the_like_flag(tmp_path: Path):
    """The star endpoint owns `liked`; the event is only a behavioural signal."""
    store, track_id, _release_id, _artist_id = _store_with_track(tmp_path)

    store.record_playback_event(
        PlaybackEventCreate(event_type="liked", track_id=track_id)
    )

    pref = store.get_track_preference(track_id)
    assert pref.liked is False
    assert pref.score > 0


def test_dislike_event_does_not_clear_a_navidrome_backed_like(tmp_path: Path):
    """A local dislike must not fight the mirror — the next sync would undo it."""
    store, track_id, _release_id, _artist_id = _store_with_track(tmp_path)
    store.set_track_liked(track_id, True)

    store.record_playback_event(
        PlaybackEventCreate(event_type="disliked", track_id=track_id)
    )

    pref = store.get_track_preference(track_id)
    assert pref.disliked is True
    assert pref.liked is True


def test_recompute_preserves_likes_it_cannot_rebuild(tmp_path: Path):
    """Replaying events cannot reconstruct likes — they must be carried across."""
    store, track_id, release_id, artist_id = _store_with_track(tmp_path)
    store.set_track_liked(track_id, True)
    store.set_release_liked(release_id, True)
    store.set_artist_liked(artist_id, True)
    store.record_playback_event(
        PlaybackEventCreate(event_type="play_threshold_reached", track_id=track_id)
    )

    store.recompute_user_preferences()

    assert _liked(store, track_id, release_id, artist_id) == (True, True, True)


def test_recompute_keeps_the_original_liked_at(tmp_path: Path):
    store, _track_id, _release_id, artist_id = _store_with_track(tmp_path)
    store.set_artist_liked(artist_id, True)
    with store.connect() as conn:
        before = conn.execute(
            "SELECT liked_at FROM user_artist_preferences WHERE artist_id = ?",
            (artist_id,),
        ).fetchone()["liked_at"]

    store.recompute_user_preferences()

    with store.connect() as conn:
        after = conn.execute(
            "SELECT liked_at FROM user_artist_preferences WHERE artist_id = ?",
            (artist_id,),
        ).fetchone()["liked_at"]
    assert after == before


def test_recompute_drops_behavioural_state_of_unliked_rows(tmp_path: Path):
    """The carry-over must not resurrect rows that were never liked."""
    store, track_id, _release_id, artist_id = _store_with_track(tmp_path)
    store.record_playback_event(
        PlaybackEventCreate(event_type="play_threshold_reached", track_id=track_id)
    )

    store.recompute_user_preferences()

    assert store.get_artist_preference(artist_id).liked is False
