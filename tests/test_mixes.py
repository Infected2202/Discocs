from pathlib import Path
import json

import numpy as np

from app.config import Settings
from app.mixes import build_taste_regions, generate_mixes, resolve_mix_settings
from app.scanner import ScannedTrack
from app.store import Store


def app_settings(tmp_path: Path) -> Settings:
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
    artist: str,
    album: str,
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


def mark_positive(store: Store, track_id: int, event_type: str = "completed") -> None:
    store.record_playback_event(
        track_id=track_id,
        event_type=event_type,
        position_seconds=180.0,
        duration_seconds=180.0,
        play_fraction=1.0,
    )


def test_taste_region_builder_preserves_small_regions(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    first = add_track(store, tmp_path, "north", [1.0, 0.0], artist="North", album="A")
    second = add_track(store, tmp_path, "east", [0.0, 1.0], artist="East", album="B")
    mark_positive(store, first)
    mark_positive(store, second)

    regions, diagnostics = build_taste_regions(store, resolve_mix_settings({"region_threshold": 0.95}))

    assert diagnostics["seed_count"] == 2
    assert len(regions) == 2
    assert sorted(len(region.seeds) for region in regions) == [1, 1]


def test_generated_mixes_apply_caps_and_cross_mix_deduplication(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    ids = [
        add_track(store, tmp_path, "seed-a", [1.0, 0.0], artist="A", album="A1"),
        add_track(store, tmp_path, "near-a", [0.98, 0.02], artist="A", album="A1"),
        add_track(store, tmp_path, "near-b", [0.95, 0.05], artist="B", album="B1"),
        add_track(store, tmp_path, "seed-c", [0.0, 1.0], artist="C", album="C1"),
        add_track(store, tmp_path, "near-c", [0.02, 0.98], artist="C", album="C1"),
        add_track(store, tmp_path, "near-d", [0.05, 0.95], artist="D", album="D1"),
    ]
    mark_positive(store, ids[0], "liked")
    mark_positive(store, ids[3], "liked")

    result = generate_mixes(
        store,
        app_settings(tmp_path),
        {"max_per_artist": 1, "max_per_release": 1, "region_threshold": 0.9},
        count=2,
        tracks_per_mix=2,
        force=True,
    )

    assert len(result.mixes) == 2
    item_sets = [store.list_generated_mix_items(mix.id) for mix in result.mixes]
    assert all(len(items) == 2 for items in item_sets)
    all_track_ids = [item.track_id for items in item_sets for item in items]
    assert len(all_track_ids) == len(set(all_track_ids))
    for mix, items in zip(result.mixes, item_sets, strict=True):
        summary = json.loads(mix.score_summary_json or "{}")
        assert summary["selected_count"] == 2
        assert summary["top_artists"]
        assert all(item.reason_json for item in items)
        assert all(item.score_breakdown_json for item in items)


def test_generated_mix_storage_round_trip_and_save(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    track_id = add_track(store, tmp_path, "one", [1.0, 0.0], artist="Artist", album="Album")

    mix = store.save_generated_mix(
        mix_id="mix-test",
        title="Test Mix",
        mix_type="debug",
        anchor={"seed_track_ids": [track_id]},
        settings={"tracks_per_mix": 1},
        score_summary={"selected_count": 1},
        items=[
            {
                "track_id": track_id,
                "score": 0.9,
                "score_breakdown": {"region_similarity": 0.9},
                "reason": {"anchor_track_id": track_id},
            }
        ],
    )
    saved = store.save_generated_mix_as_playlist(mix.id)

    assert store.get_generated_mix("mix-test").title == "Test Mix"
    assert store.list_generated_mix_items("mix-test")[0].track_id == track_id
    assert saved is not None
    assert saved.status == "saved"
