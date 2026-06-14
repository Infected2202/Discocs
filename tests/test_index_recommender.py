from pathlib import Path

import numpy as np

from app.config import Settings
from app.embedder import blend_embeddings
from app.recommender import Recommender, build_index
from app.scanner import ScannedTrack
from app.store import Store


def scanned(
    tmp_path: Path,
    name: str,
    artist: str,
    *,
    title: str | None = None,
    album: str | None = None,
) -> ScannedTrack:
    path = (tmp_path / f"{name}.flac").resolve()
    path.write_bytes(b"fake")
    stat = path.stat()
    return ScannedTrack(
        path=path,
        artist=artist,
        title=title or name,
        album=album,
        duration=180.0,
        file_size=stat.st_size,
        mtime=int(stat.st_mtime),
    )


def test_build_index_and_query_with_small_catalog(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    settings = Settings(
        data_dir=tmp_path,
        db_path=tmp_path / "app.db",
        model_dir=tmp_path / "models",
        index_dir=tmp_path,
    )

    first_id, _ = store.upsert_track(scanned(tmp_path, "seed", "A"))
    second_id, _ = store.upsert_track(scanned(tmp_path, "near", "B"))
    store.save_embedding(first_id, "discogs_multi", np.array([1.0, 0.0], dtype=np.float32))
    store.save_embedding(second_id, "discogs_multi", np.array([0.9, 0.1], dtype=np.float32))

    build_index(store, settings, "discogs_multi")
    seed = store.get_track(first_id)
    assert seed is not None

    results = Recommender(store, settings, "discogs_multi").similar(seed, k=30)

    assert [result.track.id for result in results] == [second_id]


def test_similar_mix_blends_seeds_and_excludes_them(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    settings = Settings(
        data_dir=tmp_path,
        db_path=tmp_path / "app.db",
        model_dir=tmp_path / "models",
        index_dir=tmp_path,
    )

    first_id, _ = store.upsert_track(scanned(tmp_path, "seed-a", "A"))
    second_id, _ = store.upsert_track(scanned(tmp_path, "seed-b", "B"))
    third_id, _ = store.upsert_track(scanned(tmp_path, "near", "C"))
    store.save_embedding(first_id, "discogs_multi", np.array([1.0, 0.0], dtype=np.float32))
    store.save_embedding(second_id, "discogs_multi", np.array([0.0, 1.0], dtype=np.float32))
    store.save_embedding(third_id, "discogs_multi", np.array([0.7, 0.7], dtype=np.float32))

    build_index(store, settings, "discogs_multi")
    seeds = [store.get_track(first_id), store.get_track(second_id)]
    assert seeds[0] is not None and seeds[1] is not None

    results, skipped = Recommender(store, settings, "discogs_multi").similar_mix(
        [seeds[0], seeds[1]],
        k=5,
        exclude_same_album=True,
    )

    assert skipped == []
    assert first_id not in {result.track.id for result in results}
    assert second_id not in {result.track.id for result in results}
    assert [result.track.id for result in results] == [third_id]

    blended = blend_embeddings(
        [
            store.load_embedding(first_id, "discogs_multi"),
            store.load_embedding(second_id, "discogs_multi"),
        ]
    )
    direct = Recommender(store, settings, "discogs_multi").similar_vector(
        blended,
        exclude_track_ids={first_id, second_id},
        album_seeds=[seeds[0], seeds[1]],
        k=5,
        exclude_same_album=True,
    )
    assert [result.track.id for result in direct] == [third_id]


def test_similar_mix_excludes_duplicate_seed_tracks_from_other_releases(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    settings = Settings(
        data_dir=tmp_path,
        db_path=tmp_path / "app.db",
        model_dir=tmp_path / "models",
        index_dir=tmp_path,
    )

    seed_id, _ = store.upsert_track(
        scanned(tmp_path, "seed", "Bicep", title="Glue", album="Glue EP")
    )
    duplicate_id, _ = store.upsert_track(
        scanned(tmp_path, "duplicate", " bicep ", title="  Glue  ", album="Water")
    )
    result_id, _ = store.upsert_track(
        scanned(tmp_path, "near", "Other Artist", title="Near", album="Other Release")
    )
    store.save_embedding(seed_id, "discogs_multi", np.array([1.0, 0.0], dtype=np.float32))
    store.save_embedding(duplicate_id, "discogs_multi", np.array([0.99, 0.01], dtype=np.float32))
    store.save_embedding(result_id, "discogs_multi", np.array([0.9, 0.1], dtype=np.float32))

    build_index(store, settings, "discogs_multi")
    seed = store.get_track(seed_id)
    assert seed is not None

    results, skipped = Recommender(store, settings, "discogs_multi").similar_mix(
        [seed],
        k=5,
        exclude_same_album=True,
    )

    assert skipped == []
    assert duplicate_id not in {result.track.id for result in results}
    assert [result.track.id for result in results] == [result_id]


def test_similar_mix_deduplicates_returned_tracks_across_releases(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    settings = Settings(
        data_dir=tmp_path,
        db_path=tmp_path / "app.db",
        model_dir=tmp_path / "models",
        index_dir=tmp_path,
    )

    seed_id, _ = store.upsert_track(
        scanned(tmp_path, "runaway", "AURORA", title="Runaway", album="Running With The Wolves (EP)")
    )
    first_home_id, _ = store.upsert_track(
        scanned(tmp_path, "home-deluxe", "AURORA", title="Home", album="All My Demons Greeting Me As A Friend (Deluxe)")
    )
    second_home_id, _ = store.upsert_track(
        scanned(tmp_path, "home-compilation", " aurora ", title="  Home  ", album="FOR THE HUMANS WHO TAKE LONG WALKS IN THE FOREST")
    )
    different_title_id, _ = store.upsert_track(
        scanned(tmp_path, "running", "AURORA", title="Running With The Wolves", album="Running With The Wolves (EP)")
    )
    store.save_embedding(seed_id, "discogs_multi", np.array([1.0, 0.0], dtype=np.float32))
    store.save_embedding(first_home_id, "discogs_multi", np.array([0.99, 0.01], dtype=np.float32))
    store.save_embedding(second_home_id, "discogs_multi", np.array([0.98, 0.02], dtype=np.float32))
    store.save_embedding(different_title_id, "discogs_multi", np.array([0.9, 0.1], dtype=np.float32))

    build_index(store, settings, "discogs_multi")
    seed = store.get_track(seed_id)
    assert seed is not None

    results, skipped = Recommender(store, settings, "discogs_multi").similar_mix(
        [seed],
        k=5,
        max_per_artist=3,
        exclude_same_album=False,
    )

    assert skipped == []
    assert second_home_id not in {result.track.id for result in results}
    assert [result.track.id for result in results] == [first_home_id, different_title_id]


def test_similar_mix_artist_cap_counts_collaboration_artists(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    settings = Settings(
        data_dir=tmp_path,
        db_path=tmp_path / "app.db",
        model_dir=tmp_path / "models",
        index_dir=tmp_path,
    )

    seed_id, _ = store.upsert_track(scanned(tmp_path, "seed", "Seed Artist"))
    first_aurora_id, _ = store.upsert_track(
        scanned(tmp_path, "home", "AURORA", title="Home")
    )
    second_aurora_id, _ = store.upsert_track(
        scanned(tmp_path, "running", "AURORA", title="Running With The Wolves")
    )
    collaboration_id, _ = store.upsert_track(
        scanned(tmp_path, "everything", "AURORA \u2022 Pomme", title="Everything Matters")
    )
    other_id, _ = store.upsert_track(
        scanned(tmp_path, "other", "Other Artist", title="Other")
    )
    store.save_embedding(seed_id, "discogs_multi", np.array([1.0, 0.0], dtype=np.float32))
    store.save_embedding(first_aurora_id, "discogs_multi", np.array([0.99, 0.01], dtype=np.float32))
    store.save_embedding(second_aurora_id, "discogs_multi", np.array([0.98, 0.02], dtype=np.float32))
    store.save_embedding(collaboration_id, "discogs_multi", np.array([0.97, 0.03], dtype=np.float32))
    store.save_embedding(other_id, "discogs_multi", np.array([0.9, 0.1], dtype=np.float32))

    build_index(store, settings, "discogs_multi")
    seed = store.get_track(seed_id)
    assert seed is not None

    results, skipped = Recommender(store, settings, "discogs_multi").similar_mix(
        [seed],
        k=5,
        max_per_artist=2,
        exclude_same_album=False,
    )

    assert skipped == []
    assert collaboration_id not in {result.track.id for result in results}
    assert [result.track.id for result in results] == [
        first_aurora_id,
        second_aurora_id,
        other_id,
    ]

    legacy_results, legacy_skipped = Recommender(store, settings, "discogs_multi").similar_mix(
        [seed],
        k=5,
        max_per_artist=2,
        exclude_same_album=False,
        count_collaboration_artists=False,
    )

    assert legacy_skipped == []
    assert [result.track.id for result in legacy_results] == [
        first_aurora_id,
        second_aurora_id,
        collaboration_id,
        other_id,
    ]
