from pathlib import Path

import numpy as np

from app.navidrome import NavidromeSong
from app.navidrome_starred import (
    build_starred_catalog,
    build_starred_track_ids_from_songs,
    normalize_starred_at,
    ready_tracks_from_starred_catalog,
    sync_likes_from_starred_payload,
)
from app.models import utc_now
from app.scanner import ScannedTrack
from app.store import Store


def test_build_starred_catalog_counts_statuses(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    track_ready, _ = store.upsert_track(
        ScannedTrack(
            path="navidrome://like-ready",  # type: ignore[arg-type]
            artist="A",
            title="Ready",
            album="Album",
            duration=120.0,
            file_size=1,
            mtime=1,
        )
    )
    track_missing, _ = store.upsert_track(
        ScannedTrack(
            path="navidrome://like-missing",  # type: ignore[arg-type]
            artist="B",
            title="Missing",
            album="Other",
            duration=120.0,
            file_size=1,
            mtime=1,
        )
    )
    store.upsert_external_track("navidrome", "like-ready", track_ready)
    store.upsert_external_track("navidrome", "like-missing", track_missing)
    store.save_embedding(track_ready, "discogs_multi", np.array([1.0, 0.0], dtype=np.float32))

    class FakeClient:
        def get_starred_songs(self) -> list[NavidromeSong]:
            return [
                NavidromeSong(id="like-ready", title="Ready", artist="A"),
                NavidromeSong(id="like-missing", title="Missing", artist="B"),
                NavidromeSong(id="not-synced", title="Ghost", artist="C"),
            ]

    catalog = build_starred_catalog(
        store,
        FakeClient(),  # type: ignore[arg-type]
        model="discogs_multi",
        user="alice",
    )

    assert catalog["user"] == "alice"
    assert catalog["count"] == 3
    assert catalog["mapped_count"] == 2
    assert catalog["ready_count"] == 1
    assert catalog["missing_embedding_count"] == 1
    assert catalog["not_synced_count"] == 1

    ready_tracks = ready_tracks_from_starred_catalog(catalog, store, "discogs_multi")
    assert [track.id for track in ready_tracks] == [track_ready]


def test_build_starred_track_ids_from_prefetched_songs(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    track_id, _ = store.upsert_track(
        ScannedTrack(
            path="navidrome://like-ready",  # type: ignore[arg-type]
            artist="A",
            title="Ready",
            album="Album",
            duration=120.0,
            file_size=1,
            mtime=1,
        )
    )
    store.upsert_external_track("navidrome", "like-ready", track_id)

    result = build_starred_track_ids_from_songs(
        store,
        [
            NavidromeSong(id="like-ready", title="Ready"),
            NavidromeSong(id="not-synced", title="Ghost"),
        ],
        user="alice",
    )

    assert result == {
        "user": "alice",
        "count": 2,
        "mapped_count": 1,
        "track_ids": [track_id],
        "item_ids": ["like-ready", "not-synced"],
        "not_synced_item_ids": ["not-synced"],
    }


def test_normalize_starred_at_matches_local_like_dates():
    assert normalize_starred_at("2024-03-01T10:00:00.5Z") == "2024-03-01T10:00:00.500000+00:00"
    assert normalize_starred_at("2024-03-01T13:00:00+03:00") == "2024-03-01T10:00:00.000000+00:00"
    assert normalize_starred_at("yesterday") is None
    assert normalize_starred_at(None) is None


def test_sync_from_starred_payload_orders_likes_by_star_date(tmp_path: Path):
    root = Store(tmp_path / "app.db")
    root.init()
    store = root.for_user(root.upsert_user("alice", now=utc_now()))
    track_ids = []
    for item_id in ("song-old", "song-new"):
        track_id, _ = root.upsert_track(
            ScannedTrack(
                path=(tmp_path / f"{item_id}.flac").resolve(),
                artist="A",
                title=item_id,
                album="Album",
                duration=120.0,
                file_size=1,
                mtime=1,
            )
        )
        root.upsert_external_track("navidrome", item_id, track_id)
        track_ids.append(track_id)
    old_id, new_id = track_ids

    sync_likes_from_starred_payload(
        store,
        {
            "songs": [
                {"id": "song-old", "title": "song-old", "starred": "2023-01-01T00:00:00Z"},
                {"id": "song-new", "title": "song-new", "starred": "2025-01-01T00:00:00Z"},
            ],
            "albums": [],
            "artists": [],
        },
        user="alice",
    )

    assert [track.id for track in store.list_liked_tracks()] == [new_id, old_id]
