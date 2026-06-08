from pathlib import Path

import numpy as np

from app.navidrome import NavidromeSong
from app.navidrome_starred import build_starred_catalog, ready_tracks_from_starred_catalog
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
