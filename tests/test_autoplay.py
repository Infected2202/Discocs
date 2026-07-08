from pathlib import Path
from dataclasses import replace
import json

import numpy as np

from app.autoplay import (
    apply_autoplay_caps,
    AutoplayCandidate,
    build_source_context,
    generate_autoplay_candidates,
    refill_autoplay_queue,
    resolve_autoplay_settings,
)
from app.config import Settings
from app.recommender import build_index
from app.scanner import ScannedTrack
from app.store import Store


def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        db_path=tmp_path / "app.db",
        model_dir=tmp_path / "models",
        index_dir=tmp_path,
    )


def add_track(
    store: Store,
    tmp_path: Path,
    name: str,
    vector: list[float],
    *,
    artist: str = "Artist",
    album: str = "Album",
) -> int:
    path = tmp_path / f"{name}.flac"
    path.write_bytes(b"fake")
    stat = path.stat()
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=path,
            artist=artist,
            title=name,
            album=album,
            duration=180.0,
            file_size=stat.st_size,
            mtime=int(stat.st_mtime),
        )
    )
    store.save_embedding(track_id, "discogs_multi", np.array(vector, dtype=np.float32))
    return track_id


def test_autoplay_refill_appends_source_aware_items_and_hides_debug_by_default(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    app_settings = settings(tmp_path)
    seed_id = add_track(store, tmp_path, "seed", [1.0, 0.0], artist="Seed", album="Source")
    near_id = add_track(store, tmp_path, "near", [0.98, 0.02], artist="Near", album="Near Album")
    far_id = add_track(store, tmp_path, "far", [0.7, 0.3], artist="Far", album="Far Album")
    build_index(store, app_settings, "discogs_multi")
    session, _queue = store.create_playback_session(
        source_type="track",
        source_id=seed_id,
        track_ids=[seed_id],
        autoplay_enabled=True,
    )

    result = refill_autoplay_queue(
        store,
        app_settings,
        session,
        {"source_weight": 0.8, "personal_weight": 0.2},
        visible_buffer=2,
        candidate_count=10,
        include_debug=False,
    )

    assert [item.track_id for item in result.added_items] == [near_id, far_id]
    assert all(item.origin == "autoplay" for item in result.added_items)
    assert all(item.source_type == "track" for item in result.added_items)
    assert all(item.reason for item in result.added_items)
    assert result.debug is None


def test_autoplay_context_uses_manual_queue_and_preserves_existing_upcoming(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    app_settings = settings(tmp_path)
    first_id = add_track(store, tmp_path, "first", [1.0, 0.0], album="One")
    manual_id = add_track(store, tmp_path, "manual", [0.9, 0.1], album="Two")
    candidate_id = add_track(store, tmp_path, "candidate", [0.8, 0.2], album="Three")
    build_index(store, app_settings, "discogs_multi")
    session, _queue = store.create_playback_session(
        source_type="manual",
        track_ids=[first_id],
        autoplay_enabled=True,
    )
    store.append_queue_items(session.id, [{"track_id": manual_id, "origin": "manual"}])
    session = store.get_playback_session(session.id)
    assert session is not None

    context = build_source_context(store, session)
    result = refill_autoplay_queue(store, app_settings, session, visible_buffer=1, candidate_count=10)

    assert context.seed_track_ids == [first_id, manual_id]
    assert result.added_items == []
    assert [item.track_id for item in store.list_queue_items(session.id)] == [first_id, manual_id]
    refreshed = store.get_playback_session(session.id)
    assert refreshed is not None
    state = json.loads(refreshed.state_json or "{}")
    assert [item["track_id"] for item in state["autoplay_pool"]] == [candidate_id]


def test_autoplay_refill_consumes_prepared_pool_without_rebuilding_candidates(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    app_settings = settings(tmp_path)
    seed_id = add_track(store, tmp_path, "seed", [1.0, 0.0], album="Seed")
    first_id = add_track(store, tmp_path, "pool-first", [0.9, 0.1], album="One")
    second_id = add_track(store, tmp_path, "pool-second", [0.8, 0.2], album="Two")
    third_id = add_track(store, tmp_path, "pool-third", [0.7, 0.3], album="Three")
    session, _queue = store.create_playback_session(
        source_type="track",
        source_id=seed_id,
        track_ids=[seed_id],
        autoplay_enabled=True,
        state={
            "autoplay_pool": [
                {"track_id": first_id, "origin": "autoplay", "reason": "prepared first"},
                {"track_id": second_id, "origin": "autoplay", "reason": "prepared second"},
                {"track_id": third_id, "origin": "autoplay", "reason": "prepared third"},
            ]
        },
    )

    result = refill_autoplay_queue(
        store,
        app_settings,
        session,
        visible_buffer=1,
        candidate_count=10,
        include_debug=True,
    )

    assert [item.track_id for item in result.added_items] == [first_id]
    assert result.debug is not None
    assert result.debug["generated_pool_requested"] is False
    assert result.debug["generated_pool_count"] == 0
    refreshed = store.get_playback_session(session.id)
    assert refreshed is not None
    state = json.loads(refreshed.state_json or "{}")
    assert [item["track_id"] for item in state["autoplay_pool"]] == [second_id, third_id]


def test_autoplay_source_context_supports_release_artist_playlist_search_and_generated_mix(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    release_first_id = add_track(store, tmp_path, "release-first", [1.0, 0.0], artist="Release Artist", album="Release")
    release_second_id = add_track(store, tmp_path, "release-second", [0.99, 0.01], artist="Release Artist", album="Release")
    artist_extra_id = add_track(store, tmp_path, "artist-extra", [0.97, 0.03], artist="Release Artist", album="Other")
    playlist_first_id = add_track(store, tmp_path, "playlist-first", [0.7, 0.3], artist="Playlist", album="One")
    playlist_second_id = add_track(store, tmp_path, "playlist-second", [0.6, 0.4], artist="Playlist", album="Two")
    search_first_id = add_track(store, tmp_path, "search-first", [0.5, 0.5], artist="Search", album="One")
    search_second_id = add_track(store, tmp_path, "search-second", [0.4, 0.6], artist="Search", album="Two")
    mix_first_id = add_track(store, tmp_path, "mix-first", [0.3, 0.7], artist="Mix", album="One")
    mix_second_id = add_track(store, tmp_path, "mix-second", [0.2, 0.8], artist="Mix", album="Two")
    release_id = store.search_entities("Release")["releases"]["items"][0].release.id
    artist_id = store.search_entities("Release Artist")["artists"]["items"][0].artist.id
    with store.connect() as conn:
        cursor = conn.execute(
            "INSERT INTO playlists (title, kind, source_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("Playlist", "manual", "{}", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        playlist_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO playlist_items (playlist_id, position, track_id, created_at) VALUES (?, ?, ?, ?)",
            (playlist_id, 0, playlist_first_id, "2026-01-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO playlist_items (playlist_id, position, track_id, created_at) VALUES (?, ?, ?, ?)",
            (playlist_id, 1, playlist_second_id, "2026-01-01T00:00:00+00:00"),
        )
    store.save_generated_mix(
        mix_id="mix-phase-6",
        title="Phase 6 Mix",
        mix_type="debug",
        items=[
            {"track_id": mix_first_id, "score": 1.0},
            {"track_id": mix_second_id, "score": 0.9},
        ],
    )

    release_session, _queue = store.create_playback_session(source_type="release", source_id=release_id)
    artist_session, _queue = store.create_playback_session(source_type="artist", source_id=artist_id)
    playlist_session, _queue = store.create_playback_session(source_type="playlist", source_id=playlist_id)
    search_session, _queue = store.create_playback_session(
        source_type="search",
        source_label="Search",
        track_ids=[search_first_id, search_second_id],
    )
    mix_session, _queue = store.create_playback_session(
        source_type="generated_mix",
        settings={"generated_mix_id": "mix-phase-6"},
    )

    release_context = build_source_context(store, release_session)
    artist_context = build_source_context(store, artist_session)
    playlist_context = build_source_context(store, playlist_session)
    search_context = build_source_context(store, search_session)
    mix_context = build_source_context(store, mix_session)

    assert release_context.source_track_ids == [release_first_id, release_second_id]
    assert artist_context.source_track_ids == [release_first_id, release_second_id, artist_extra_id]
    assert playlist_context.source_track_ids == [playlist_first_id, playlist_second_id]
    assert search_context.source_track_ids == [search_first_id, search_second_id]
    assert mix_context.source_track_ids == [mix_first_id, mix_second_id]
    assert mix_context.source_debug["strategy"] == "generated_mix_items"


def test_autoplay_source_context_merges_seed_and_exclude_ids_without_duplicates(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    first_id = add_track(store, tmp_path, "first", [1.0, 0.0], artist="Artist", album="One")
    second_id = add_track(store, tmp_path, "second", [0.9, 0.1], artist="Artist", album="Two")
    session, queue = store.create_playback_session(
        source_type="track",
        source_id=first_id,
        track_ids=[first_id],
        autoplay_enabled=True,
    )
    store.append_queue_items(session.id, [{"track_id": second_id, "origin": "manual"}])
    store.record_playback_event(
        session_id=session.id,
        queue_item_id=queue[0].id,
        track_id=first_id,
        event_type="completed",
        play_fraction=1.0,
    )
    refreshed = store.get_playback_session(session.id)
    assert refreshed is not None

    context = build_source_context(store, replace(refreshed, current_track_id=second_id))

    assert context.seed_track_ids == [first_id]
    assert context.exclude_track_ids == {first_id, second_id}


def test_autoplay_skip_penalty_is_session_local_and_does_not_blacklist_globally(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    app_settings = settings(tmp_path)
    seed_id = add_track(store, tmp_path, "seed", [1.0, 0.0], album="Seed")
    skipped_id = add_track(store, tmp_path, "skipped", [0.96, 0.04], album="Skipped")
    close_to_skip_id = add_track(store, tmp_path, "close-to-skip", [0.95, 0.05], album="Close")
    less_close_id = add_track(store, tmp_path, "less-close", [0.75, 0.25], album="Less Close")
    build_index(store, app_settings, "discogs_multi")
    session, _queue = store.create_playback_session(
        source_type="track",
        source_id=seed_id,
        track_ids=[seed_id],
        autoplay_enabled=True,
    )
    store.record_playback_event(
        session_id=session.id,
        track_id=skipped_id,
        event_type="skipped",
        play_fraction=0.1,
    )
    session = store.get_playback_session(session.id)
    assert session is not None

    autoplay_settings = resolve_autoplay_settings(
        session,
        {"recent_skip_penalty": 2.0, "source_weight": 0.8, "personal_weight": 0.0},
        candidate_count=10,
    )
    candidates, context, _debug = generate_autoplay_candidates(
        store,
        app_settings,
        session,
        autoplay_settings,
    )
    ordered_ids = [candidate.track.id for candidate in candidates]

    assert skipped_id in context.exclude_track_ids
    assert skipped_id not in ordered_ids
    assert ordered_ids.index(less_close_id) < ordered_ids.index(close_to_skip_id)

    fresh_session, _queue = store.create_playback_session(
        source_type="track",
        source_id=seed_id,
        track_ids=[seed_id],
        autoplay_enabled=True,
    )
    fresh_candidates, _context, _debug = generate_autoplay_candidates(
        store,
        app_settings,
        fresh_session,
        autoplay_settings,
    )
    assert skipped_id in [candidate.track.id for candidate in fresh_candidates]


def test_autoplay_source_weight_dominates_personal_bias_by_default(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    app_settings = settings(tmp_path)
    seed_id = add_track(store, tmp_path, "seed", [1.0, 0.0], artist="Seed", album="Seed")
    source_near_id = add_track(store, tmp_path, "source-near", [0.98, 0.02], artist="Near", album="Near")
    personal_fav_id = add_track(store, tmp_path, "personal-fav", [0.2, 0.8], artist="Fav", album="Fav")
    build_index(store, app_settings, "discogs_multi")
    store.record_playback_event(track_id=personal_fav_id, event_type="liked")
    session, _queue = store.create_playback_session(
        source_type="track",
        source_id=seed_id,
        track_ids=[seed_id],
        autoplay_enabled=True,
    )
    autoplay_settings = resolve_autoplay_settings(session, candidate_count=10)

    candidates, _context, debug = generate_autoplay_candidates(store, app_settings, session, autoplay_settings)
    ordered_ids = [candidate.track.id for candidate in candidates]

    assert ordered_ids.index(source_near_id) < ordered_ids.index(personal_fav_id)
    assert debug["excluded_counts"]["current"] == 1
    assert candidates[0].debug["score_breakdown"]["source_similarity"] > 0.9


def test_autoplay_caps_limit_generated_tail_by_artist_and_release(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    seed_id = add_track(store, tmp_path, "seed", [1.0, 0.0], artist="Seed", album="Seed")
    same_artist_a_id = add_track(store, tmp_path, "same-artist-a", [0.99, 0.01], artist="Same", album="A")
    same_artist_b_id = add_track(store, tmp_path, "same-artist-b", [0.98, 0.02], artist="Same", album="B")
    same_release_id = add_track(store, tmp_path, "same-release", [0.97, 0.03], artist="Other", album="A")
    other_id = add_track(store, tmp_path, "other", [0.96, 0.04], artist="Other", album="C")
    session, _queue = store.create_playback_session(source_type="track", source_id=seed_id, track_ids=[seed_id])
    autoplay_settings = resolve_autoplay_settings(session, {"max_per_artist": 1, "max_per_release": 1})
    tracks = store.get_tracks([same_artist_a_id, same_artist_b_id, same_release_id, other_id])
    candidates = [
        AutoplayCandidate(tracks[same_artist_a_id], 1.0, 0.0, 0.0, 1.0, "a", {}),
        AutoplayCandidate(tracks[same_artist_b_id], 0.9, 0.0, 0.0, 0.9, "b", {}),
        AutoplayCandidate(tracks[same_release_id], 0.8, 0.0, 0.0, 0.8, "c", {}),
        AutoplayCandidate(tracks[other_id], 0.7, 0.0, 0.0, 0.7, "d", {}),
    ]

    kept, debug = apply_autoplay_caps(store, candidates, session, autoplay_settings)

    assert [candidate.track.id for candidate in kept] == [same_artist_a_id, other_id]
    assert debug["skipped_artist_cap"] == 1
    assert debug["skipped_release_cap"] == 1


def test_autoplay_preference_chips_adjust_scoring_settings(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    seed_id = add_track(store, tmp_path, "seed", [1.0, 0.0])
    session, _queue = store.create_playback_session(source_type="track", source_id=seed_id, track_ids=[seed_id])

    default_settings = resolve_autoplay_settings(session)
    familiar = resolve_autoplay_settings(session, {"preference_chip": "Familiar"})
    recommended = resolve_autoplay_settings(session, {"preference_chip": "Recommended"})

    assert familiar.personal_weight > default_settings.personal_weight
    assert familiar.exploration_ratio < default_settings.exploration_ratio
    assert recommended.source_weight > default_settings.source_weight


def test_autoplay_filters_unavailable_tracks_from_candidates_and_pool(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    app_settings = settings(tmp_path)
    seed_id = add_track(store, tmp_path, "seed", [1.0, 0.0], artist="Seed", album="Seed")
    missing_id = add_track(store, tmp_path, "missing", [0.99, 0.01], artist="Missing", album="Missing")
    available_id = add_track(store, tmp_path, "available", [0.98, 0.02], artist="Available", album="Available")
    build_index(store, app_settings, "discogs_multi")
    store.mark_track_missing(missing_id)
    session, _queue = store.create_playback_session(
        source_type="track",
        source_id=seed_id,
        track_ids=[seed_id],
        autoplay_enabled=True,
        state={
            "autoplay_pool": [
                {"track_id": missing_id, "origin": "autoplay"},
                {"track_id": available_id, "origin": "autoplay"},
            ]
        },
    )

    result = refill_autoplay_queue(
        store,
        app_settings,
        session,
        visible_buffer=1,
        candidate_count=10,
        include_debug=True,
    )

    assert [item.track_id for item in result.added_items] == [available_id]
    assert result.debug is not None
    assert result.debug["unavailable_candidate_count"] == 1
