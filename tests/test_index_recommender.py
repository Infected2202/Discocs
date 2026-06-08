from pathlib import Path

import numpy as np

from app.config import Settings
from app.embedder import blend_embeddings
from app.recommender import Recommender, build_index
from app.scanner import ScannedTrack
from app.store import Store


def scanned(tmp_path: Path, name: str, artist: str) -> ScannedTrack:
    path = (tmp_path / f"{name}.flac").resolve()
    path.write_bytes(b"fake")
    stat = path.stat()
    return ScannedTrack(
        path=path,
        artist=artist,
        title=name,
        album=None,
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
