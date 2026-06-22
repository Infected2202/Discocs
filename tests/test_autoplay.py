from pathlib import Path
import json

import numpy as np

from app.autoplay import (
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
