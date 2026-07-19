"""Cross-user isolation invariants for every personal Store domain."""
from pathlib import Path

import pytest

from app.models import PlaybackEventCreate
from app.models import utc_now
from app.scanner import ScannedTrack
from app.store import Store
from app.services.dashboard import _dashboard_history


def _stores(tmp_path: Path) -> tuple[Store, Store, int]:
    root = Store(tmp_path / "app.db")
    root.init()
    now = utc_now()
    alice_id = root.upsert_user("alice", now=now)
    bob_id = root.upsert_user("bob", now=now)
    track_id, _ = root.upsert_track(
        ScannedTrack(
            path=(tmp_path / "track.flac").resolve(),
            artist="Scoped Artist",
            title="Scoped Track",
            album="Scoped Album",
            duration=120.0,
            file_size=1,
            mtime=1,
        )
    )
    return root.for_user(alice_id), root.for_user(bob_id), track_id


def test_unscoped_store_rejects_personal_reads(tmp_path: Path):
    store = Store(tmp_path / "app.db", user_id=None)
    store.init()

    with pytest.raises(PermissionError):
        store.get_track_preference(1)


def test_playback_and_preferences_are_user_scoped(tmp_path: Path):
    alice, bob, track_id = _stores(tmp_path)
    session, _ = alice.create_playback_session(
        source_type="track", track_ids=[track_id]
    )
    alice.record_playback_event(
        PlaybackEventCreate(
            event_type="liked", session_id=session.id, track_id=track_id
        )
    )

    assert alice.get_playback_session(session.id) is not None
    # The event carries the behavioural signal; `liked` itself is Navidrome's.
    assert alice.get_track_preference(track_id).score > 0
    assert len(alice.list_playback_events(session.id)) == 1
    assert bob.get_playback_session(session.id) is None
    assert bob.get_track_preference(track_id) is None
    assert bob.list_playback_events(session.id) == []
    with pytest.raises(ValueError):
        bob.record_playback_event(
            PlaybackEventCreate(
                event_type="liked", session_id=session.id, track_id=track_id
            )
        )


def test_dashboard_history_does_not_leak_between_users(tmp_path: Path):
    alice, bob, track_id = _stores(tmp_path)
    alice.record_playback_event(
        PlaybackEventCreate(event_type="play_threshold_reached", track_id=track_id)
    )

    alice_items, alice_total = _dashboard_history(alice, 10, 0)
    bob_items, bob_total = _dashboard_history(bob, 10, 0)

    assert alice_total == len(alice_items) == 1
    assert bob_total == 0
    assert bob_items == []


def test_recompute_preferences_cannot_delete_another_users_rows(tmp_path: Path):
    alice, bob, track_id = _stores(tmp_path)
    for store in (alice, bob):
        store.set_track_liked(track_id, True)

    alice.recompute_user_preferences()

    assert alice.get_track_preference(track_id).liked is True
    assert bob.get_track_preference(track_id).liked is True


def test_navidrome_star_sync_only_replaces_current_users_likes(tmp_path: Path):
    alice, bob, track_id = _stores(tmp_path)
    alice.set_track_liked(track_id, True)
    bob.set_track_liked(track_id, True)

    alice.sync_likes_from_navidrome(track_ids=[], release_ids=[], artist_ids=[])

    assert alice.get_track_preference(track_id).liked is False
    assert bob.get_track_preference(track_id).liked is True


def test_artist_popularity_is_an_explicit_global_sum(tmp_path: Path):
    alice, bob, track_id = _stores(tmp_path)
    alice.import_external_track_play_state(track_id, play_count=2)
    bob.import_external_track_play_state(track_id, play_count=3)
    artist_id = alice.artist_ids_for_track(track_id)[0]

    [(track, play_count)] = alice.top_tracks_for_artist(artist_id)

    assert track.id == track_id
    assert play_count == 5


def test_mixes_playlists_flow_and_cache_are_user_scoped(tmp_path: Path):
    alice, bob, track_id = _stores(tmp_path)
    mix = alice.save_generated_mix(
        mix_id="alice-mix",
        title="Alice mix",
        mix_type="taste_region",
        items=[{"track_id": track_id}],
    )
    playlist = alice.create_playlist(title="Alice playlist", track_ids=[track_id])
    profile = alice.upsert_flow_profile("discogs_multi", "ready")
    alice.set_albums_for_you_cache("discogs_multi", '[{"release_id": 1}]')

    assert alice.get_generated_mix(mix.id) is not None
    assert alice.get_playlist(playlist.id) is not None
    assert alice.get_flow_profile(profile.model_key) is not None
    assert alice.get_albums_for_you_cache("discogs_multi") is not None
    assert bob.get_generated_mix(mix.id) is None
    assert bob.list_generated_mix_items(mix.id) == []
    assert bob.get_playlist(playlist.id) is None
    assert bob.list_playlist_items(playlist.id) == []
    assert bob.get_flow_profile(profile.model_key) is None
    assert bob.get_albums_for_you_cache("discogs_multi") is None


def test_public_playlist_is_cross_user_read_only(tmp_path: Path):
    alice, bob, track_id = _stores(tmp_path)
    playlist = alice.create_playlist(
        title="Alice public",
        source={"visibility": "public"},
        track_ids=[track_id],
    )

    assert bob.get_playlist(playlist.id) is not None
    assert bob.get_owned_playlist(playlist.id) is None
    assert bob.playlist_track_ids(playlist.id) == [track_id]
    assert [item.track_id for item in bob.list_playlist_items(playlist.id)] == [track_id]
    assert bob.update_playlist(playlist.id, title="Hijacked") is None
    assert bob.remove_playlist_tracks(playlist.id, [track_id]) == 0
    assert bob.reorder_playlist_tracks(playlist.id, [track_id]) is False
    assert bob.delete_playlist(playlist.id) is False

    alice.update_playlist(playlist.id, visibility="private")
    assert bob.get_playlist(playlist.id) is None
    assert bob.playlist_track_ids(playlist.id) == []
