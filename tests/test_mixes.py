from pathlib import Path
import json
from types import SimpleNamespace

import numpy as np

from app.config import Settings
from app.mixes import (
    _prepare_candidate_source,
    build_taste_regions,
    ensure_dashboard_mixes,
    generate_mixes,
    resolve_mix_settings,
)
from app.recommender import build_index
from app.scanner import ScannedTrack
from app.serializers.mixes import generated_mix_summary_dict
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


def test_taste_regions_use_listening_history_seed_source(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    played = add_track(store, tmp_path, "played", [1.0, 0.0], artist="Played", album="History")
    liked = add_track(store, tmp_path, "liked", [0.0, 1.0], artist="Liked", album="Likes")
    store.record_playback_event(
        track_id=played,
        event_type="play_threshold_reached",
        position_seconds=45.0,
        duration_seconds=180.0,
        play_fraction=0.25,
    )
    mark_positive(store, liked, "liked")

    history_regions, history_diagnostics = build_taste_regions(
        store,
        resolve_mix_settings({"mix_seed_source": "listening_history", "mix_region_threshold": 0.95}),
    )
    liked_regions, liked_diagnostics = build_taste_regions(
        store,
        resolve_mix_settings({"mix_seed_source": "track_likes_only", "mix_region_threshold": 0.95}),
    )

    history_seed_ids = {seed.track.id for region in history_regions for seed in region.seeds}
    liked_seed_ids = {seed.track.id for region in liked_regions for seed in region.seeds}
    assert history_diagnostics["seed_count"] == 2
    assert {played, liked} <= history_seed_ids
    assert liked_diagnostics["seed_count"] == 1
    assert liked_seed_ids == {liked}


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
    build_index(store, app_settings(tmp_path), "discogs_multi")

    result = generate_mixes(
        store,
        app_settings(tmp_path),
        {"max_per_artist": 1, "max_per_release": 1, "region_threshold": 0.9},
        count=2,
        tracks_per_mix=2,
        force=True,
    )

    assert len(result.mixes) == 2
    assert result.diagnostics["candidate_source"]["type"] == "persisted"
    assert result.diagnostics["candidate_source"]["uses_hnsw"] is True
    item_sets = [store.list_generated_mix_items(mix.id) for mix in result.mixes]
    assert all(len(items) == 2 for items in item_sets)
    all_track_ids = [item.track_id for items in item_sets for item in items]
    assert len(all_track_ids) == len(set(all_track_ids))
    for mix, items in zip(result.mixes, item_sets, strict=True):
        summary = json.loads(mix.score_summary_json or "{}")
        assert summary["selected_count"] == 2
        assert summary["representative_track"]["title"]
        assert summary["seed_examples"]
        assert all(item.reason_json for item in items)
        assert all(item.score_breakdown_json for item in items)


def test_prepare_candidate_source_reports_empty_input_without_index(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()

    source = _prepare_candidate_source(
        store,
        app_settings(tmp_path),
        resolve_mix_settings({}),
        np.array([], dtype=np.int64),
        np.array([], dtype=np.float32),
    )

    assert source.index is None
    assert source.diagnostics["type"] == "none"
    assert source.diagnostics["embedding_count"] == 0
    assert source.diagnostics["uses_hnsw"] is False


def test_generated_mix_novelty_weight_promotes_unheard_candidates(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    seed = add_track(store, tmp_path, "seed", [1.0, 0.0], artist="Seed", album="A")
    recently_heard = add_track(store, tmp_path, "recent", [0.999, 0.001], artist="Recent", album="B")
    unheard = add_track(store, tmp_path, "unheard", [0.996, 0.004], artist="Unheard", album="C")
    mark_positive(store, seed, "liked")
    store.import_external_track_play_state(
        recently_heard,
        play_count=12,
        last_played_at="2026-06-24T00:00:00+00:00",
    )

    low_novelty = generate_mixes(
        store,
        app_settings(tmp_path),
        {
            "mix_region_threshold": 0.99,
            "mix_tracks_per_mix": 2,
            "mix_discovery_ratio": 0.0,
            "mix_novelty_weight": 0.0,
            "mix_max_per_artist": 10,
            "mix_max_per_release": 10,
        },
        count=1,
        tracks_per_mix=2,
        force=True,
    )
    high_novelty = generate_mixes(
        store,
        app_settings(tmp_path),
        {
            "mix_region_threshold": 0.99,
            "mix_tracks_per_mix": 2,
            "mix_discovery_ratio": 0.0,
            "mix_novelty_weight": 1.0,
            "mix_max_per_artist": 10,
            "mix_max_per_release": 10,
        },
        count=1,
        tracks_per_mix=2,
        force=True,
    )

    low_items = store.list_generated_mix_items(low_novelty.mixes[0].id)
    high_items = store.list_generated_mix_items(high_novelty.mixes[0].id)
    assert {item.track_id for item in low_items} == {seed, recently_heard}
    assert {item.track_id for item in high_items} == {seed, unheard}
    summary = json.loads(high_novelty.mixes[0].score_summary_json or "{}")
    assert summary["novelty_weight"] == 1.0
    assert summary["novelty_distribution"]["unheard"] >= 1


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
    assert saved.saved_playlist_id is not None
    playlist = store.get_playlist(saved.saved_playlist_id)
    assert playlist is not None
    assert playlist.kind == "saved_mix"
    assert [item.track_id for item in store.list_playlist_items(playlist.id)] == [track_id]


def test_dashboard_mixes_refresh_after_daily_preference_change(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    first = add_track(store, tmp_path, "first", [1.0, 0.0], artist="First", album="A")
    second = add_track(store, tmp_path, "second", [0.95, 0.05], artist="Second", album="B")
    mark_positive(store, first)
    initial = ensure_dashboard_mixes(
        store,
        app_settings(tmp_path),
        {"mix_dashboard_count": 1, "mix_tracks_per_mix": 1, "mix_update_cadence": "daily"},
    )
    assert initial.generated
    old_mix_id = initial.generated[0].id
    old_timestamp = "2026-01-01T00:00:00+00:00"
    with store.connect() as conn:
        conn.execute(
            "UPDATE generated_mixes SET updated_at = ?, expires_at = ? WHERE id = ?",
            (old_timestamp, "2999-01-01T00:00:00+00:00", old_mix_id),
        )
        conn.execute(
            "UPDATE user_track_preferences SET updated_at = ? WHERE track_id = ?",
            ("2026-01-03T00:00:00+00:00", first),
        )
    mark_positive(store, second)

    refreshed = ensure_dashboard_mixes(
        store,
        app_settings(tmp_path),
        {"mix_dashboard_count": 1, "mix_tracks_per_mix": 1, "mix_update_cadence": "daily"},
    )

    assert refreshed.diagnostics["reason"] == "preference_changed"
    assert refreshed.generated
    assert refreshed.generated[0].id != old_mix_id
    assert store.get_generated_mix(old_mix_id).status == "stale"


def test_dashboard_mixes_do_not_regenerate_when_fresh(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    first = add_track(store, tmp_path, "first", [1.0, 0.0], artist="First", album="A")
    second = add_track(store, tmp_path, "second", [0.95, 0.05], artist="Second", album="B")
    mark_positive(store, first)
    settings = {"mix_dashboard_count": 1, "mix_tracks_per_mix": 1, "mix_update_cadence": "weekly"}

    first_result = ensure_dashboard_mixes(store, app_settings(tmp_path), settings)
    second_result = ensure_dashboard_mixes(store, app_settings(tmp_path), settings)

    assert first_result.generated
    assert second_result.generated == []
    assert second_result.diagnostics["reason"] == "fresh"
    assert store.count_generated_mixes(["active"]) == 1


def test_dense_taste_region_uses_subanchors_for_multiple_mixes(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    for index in range(5):
        track_id = add_track(
            store,
            tmp_path,
            f"dense-{index}",
            [1.0, 0.01 * index],
            artist=f"Artist {index}",
            album=f"Album {index}",
        )
        mark_positive(store, track_id, "liked")

    result = generate_mixes(
        store,
        app_settings(tmp_path),
        {"mix_region_threshold": 0.999, "mix_max_per_artist": 1, "mix_max_per_release": 1},
        count=3,
        tracks_per_mix=1,
        force=True,
    )

    assert len(result.mixes) == 3
    anchors = [json.loads(mix.anchor_json or "{}") for mix in result.mixes]
    assert any(anchor["diagnostics"].get("subregion") for anchor in anchors)


def test_anchor_selection_prefers_distinct_weaker_region_over_near_duplicate(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    strong_ids = [
        add_track(store, tmp_path, "strong-a", [1.0, 0.0], artist="Seba", album="DnB A"),
        add_track(store, tmp_path, "strong-b", [0.99, 0.01], artist="Audio", album="DnB B"),
        add_track(store, tmp_path, "near-a", [0.96, 0.04], artist="Noisia", album="DnB C"),
        add_track(store, tmp_path, "near-b", [0.95, 0.05], artist="Black Sun Empire", album="DnB D"),
    ]
    rock_ids = [
        add_track(store, tmp_path, "rock-a", [0.0, 1.0], artist="Royal Blood", album="Rock A"),
        add_track(store, tmp_path, "rock-b", [0.02, 0.98], artist="Linkin Park", album="Rock B"),
    ]
    for track_id in strong_ids:
        mark_positive(store, track_id, "liked")
        mark_positive(store, track_id, "replayed")
    for track_id in rock_ids:
        mark_positive(store, track_id, "liked")

    result = generate_mixes(
        store,
        app_settings(tmp_path),
        {"mix_region_threshold": 0.98, "mix_max_per_artist": 2, "mix_max_per_release": 2},
        count=2,
        tracks_per_mix=1,
        force=True,
    )

    anchors = [json.loads(mix.anchor_json or "{}") for mix in result.mixes]
    selected_artists = {
        artist
        for anchor in anchors
        for artist in [
            anchor.get("representative_artist"),
            *(example.get("artist") for example in anchor.get("seed_examples", [])),
        ]
        if artist
    }
    assert {"Royal Blood", "Linkin Park"} & selected_artists


def test_generated_mix_listing_keeps_generation_order(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    ids = []
    for index in range(8):
        vector = [0.0] * 8
        vector[index] = 1.0
        ids.append(
            add_track(
                store,
                tmp_path,
                f"order-{index}",
                vector,
                artist=f"Artist {index}",
                album=f"Album {index}",
            )
        )
    for track_id in ids:
        mark_positive(store, track_id, "liked")

    generate_mixes(
        store,
        app_settings(tmp_path),
        {"mix_region_threshold": 0.999, "mix_max_per_artist": 1, "mix_max_per_release": 1},
        count=8,
        tracks_per_mix=1,
        force=True,
    )

    titles = [mix.title for mix in store.list_generated_mixes(statuses=["active"], limit=8)]
    assert [title.split(":", 1)[0] for title in titles] == [f"Mix {index}" for index in range(1, 9)]


def test_generated_mix_summary_uses_expected_artwork_source():
    class FakeStore:
        def __init__(self, items):
            self._items = items

        def list_generated_mix_items(self, _mix_id):
            return self._items

    base_mix = {
        "id": "mix-1",
        "title": "Mix 1",
        "mix_type": "generated",
        "status": "active",
        "anchor_json": None,
        "settings_json": None,
        "score_summary_json": None,
        "created_at": "2026-07-08T00:00:00+00:00",
        "updated_at": "2026-07-08T00:00:00+00:00",
        "expires_at": None,
        "saved_playlist_id": None,
    }

    with_cover = generated_mix_summary_dict(
        FakeStore([SimpleNamespace(track_id=42)]),
        SimpleNamespace(**base_mix, cover_path="cover.jpg"),
    )
    with_track_cover = generated_mix_summary_dict(
        FakeStore([SimpleNamespace(track_id=42)]),
        SimpleNamespace(**base_mix, cover_path=None),
    )
    without_artwork = generated_mix_summary_dict(
        FakeStore([]),
        SimpleNamespace(**base_mix, cover_path=None),
    )

    assert with_cover["artwork"] == {
        "url": "/api/v1/mixes/mix-1/cover",
        "source": "generated_mix",
        "placeholder": False,
    }
    assert with_track_cover["artwork"] == {
        "url": "/tracks/42/cover?size=512",
        "source": "track",
        "placeholder": False,
    }
    assert without_artwork["artwork"] == {
        "url": None,
        "source": "none",
        "placeholder": True,
    }


def test_generated_mix_summary_uses_track_count_when_anchor_has_no_subtitle_parts():
    class FakeStore:
        def list_generated_mix_items(self, _mix_id):
            return [SimpleNamespace(track_id=1), SimpleNamespace(track_id=2), SimpleNamespace(track_id=3)]

    summary = generated_mix_summary_dict(
        FakeStore(),
        SimpleNamespace(
            id="mix-2",
            title="Mix 2",
            mix_type="generated",
            status="active",
            cover_path=None,
            anchor_json='{"representative_artist": "", "representative_album": null}',
            settings_json=None,
            score_summary_json=None,
            created_at="2026-07-08T00:00:00+00:00",
            updated_at="2026-07-08T00:00:00+00:00",
            expires_at=None,
            saved_playlist_id=None,
        ),
    )

    assert summary["subtitle"] == "3 tracks"
