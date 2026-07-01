from __future__ import annotations

import json
from pathlib import Path

from app.library import TrackMetadataEnvelope
from app.navidrome_migration import repair_navidrome_empty_releases
from app.scanner import ScannedTrack
from app.store import Store


def _legacy_release_move(store: Store) -> tuple[int, int]:
    source_path = "/music/Artist/Album/01 - Track.flac"
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=Path("navidrome://song-1"),
            artist="Artist",
            title="Track",
            album="Old Album",
            duration=120.0,
            file_size=100,
            mtime=0,
        )
    )
    old_raw = json.dumps(
        {
            "id": "song-1",
            "albumId": "album-old",
            "album": "Old Album",
            "path": source_path,
        }
    )
    store.upsert_external_track("navidrome", "song-1", track_id, old_raw)
    old_release_id = store.upsert_normalized_track_sidecars(
        track_id,
        TrackMetadataEnvelope(
            title="Track",
            artist="Artist",
            album="Old Album",
            path="navidrome://song-1",
            provider="navidrome",
            provider_track_id="song-1",
            provider_release_id="album-old",
            raw_json=old_raw,
        ),
    )

    current_raw = json.dumps(
        {
            "id": "song-1",
            "albumId": "album-new",
            "album": "New Album",
            "path": source_path,
        }
    )
    store.upsert_external_track("navidrome", "song-1", track_id, current_raw)
    target_release_id = store.upsert_normalized_track_sidecars(
        track_id,
        TrackMetadataEnvelope(
            title="Track",
            artist="Artist",
            album="New Album",
            path="navidrome://song-1",
            provider="navidrome",
            provider_track_id="song-1",
            provider_release_id="album-new",
            raw_json=current_raw,
        ),
    )
    return old_release_id, target_release_id


def test_release_repair_dry_run_does_not_change_database(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    old_release_id, target_release_id = _legacy_release_move(store)

    result = repair_navidrome_empty_releases(store)

    assert result.dry_run is True
    assert result.matched == 1
    assert result.merged == 0
    assert store.get_release(old_release_id) is not None
    assert store.get_release(target_release_id) is not None


def test_release_repair_merges_state_and_keeps_old_provider_alias(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    old_release_id, target_release_id = _legacy_release_move(store)
    now = "2026-06-29T00:00:00+00:00"
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO user_release_preferences (
                release_id, liked, play_count, completion_count, skip_count,
                score, updated_at
            )
            VALUES (?, 1, 3, 2, 1, 1.5, ?), (?, 0, 4, 1, 2, 2.0, ?)
            """,
            (old_release_id, now, target_release_id, now),
        )
        conn.execute(
            """
            INSERT INTO playback_events (
                id, release_id, event_type, created_at, source
            )
            VALUES ('event-1', ?, 'played', ?, 'web')
            """,
            (old_release_id, now),
        )
        conn.execute(
            """
            INSERT INTO playback_sessions (
                id, source_type, source_id, source_label, mode, status,
                autoplay_enabled, shuffle_enabled, repeat_mode, started_at,
                updated_at, state_json
            )
            VALUES ('session-1', 'release', ?, 'Old Album', 'ordered', 'active',
                    0, 0, 'off', ?, ?, ?)
            """,
            (
                old_release_id,
                now,
                now,
                json.dumps({"session_release_plays": {str(old_release_id): 2}}),
            ),
        )
        track_id = conn.execute(
            "SELECT track_id FROM release_tracks WHERE release_id = ?",
            (target_release_id,),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO queue_items (
                id, session_id, track_id, position, origin, source_type,
                source_id, status, created_at, updated_at
            )
            VALUES ('queue-1', 'session-1', ?, 0, 'manual', 'release', ?,
                    'queued', ?, ?)
            """,
            (track_id, old_release_id, now, now),
        )

    result = repair_navidrome_empty_releases(store, dry_run=False)

    assert result.merged == 1
    assert store.get_release(old_release_id) is None
    assert store.entity_id_for_external_id(
        "navidrome", "release", "album-old"
    ) == target_release_id
    with store.connect() as conn:
        preference = conn.execute(
            "SELECT * FROM user_release_preferences WHERE release_id = ?",
            (target_release_id,),
        ).fetchone()
        assert preference["liked"] == 1
        assert preference["play_count"] == 7
        assert preference["completion_count"] == 3
        assert preference["skip_count"] == 3
        assert preference["score"] == 3.5
        assert conn.execute(
            "SELECT release_id FROM playback_events WHERE id = 'event-1'"
        ).fetchone()[0] == target_release_id
        session = conn.execute(
            "SELECT source_id, source_label, state_json FROM playback_sessions WHERE id = 'session-1'"
        ).fetchone()
        assert session["source_id"] == target_release_id
        assert session["source_label"] == "New Album"
        assert json.loads(session["state_json"])["session_release_plays"] == {
            str(target_release_id): 2
        }
        assert conn.execute(
            "SELECT source_id FROM queue_items WHERE id = 'queue-1'"
        ).fetchone()[0] == target_release_id


def test_release_repair_rejects_changed_sample_path(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    old_release_id, _target_release_id = _legacy_release_move(store)
    with store.connect() as conn:
        raw = json.loads(
            conn.execute(
                """
                SELECT raw_json FROM external_ids
                WHERE entity_type = 'release' AND entity_id = ?
                """,
                (old_release_id,),
            ).fetchone()[0]
        )
        raw["path"] = "/music/Other/Edition/01 - Track.flac"
        conn.execute(
            """
            UPDATE external_ids SET raw_json = ?
            WHERE entity_type = 'release' AND entity_id = ?
            """,
            (json.dumps(raw), old_release_id),
        )

    result = repair_navidrome_empty_releases(store)

    assert result.matched == 0
    assert result.path_mismatch == 1
