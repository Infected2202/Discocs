from pathlib import Path

import numpy as np
import pytest

from app.scanner import ScannedTrack
from app.store import Store


def _make_track(store: Store, tmp_path: Path, name: str) -> int:
    scanned = ScannedTrack(
        path=(tmp_path / f"{name}.flac").resolve(),
        artist=f"Artist {name}",
        title=f"Title {name}",
        album=f"Album {name}",
        genre="Techno",
        year=2000,
        duration=100.0,
        file_size=len(name),
        mtime=1,
    )
    track_id, _ = store.upsert_track(scanned)
    return track_id


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "app.db")
    store.init()
    return store


def test_create_get_and_list_projections(tmp_path: Path):
    store = _store(tmp_path)

    p1 = store.create_map_projection(
        model_name="discogs_multi",
        name="umap_local",
        method="umap",
        params_json='{"n_neighbors": 15}',
        embedding_dim=400,
        source_embedding_count=1234,
    )
    assert p1.status == "pending"
    assert p1.metric == "cosine"
    assert p1.version == 1
    assert p1.embedding_dim == 400
    assert p1.projected_count == 0
    assert p1.completed_at is None

    fetched = store.get_map_projection(p1.id)
    assert fetched == p1

    # A second projection for the same model, and one for another model.
    store.create_map_projection(
        model_name="discogs_multi", name="pca", method="pca", metric="euclidean"
    )
    store.create_map_projection(
        model_name="muq_mulan", name="umap_local", method="umap"
    )

    assert len(store.list_map_projections()) == 3
    multi = store.list_map_projections("discogs_multi")
    assert len(multi) == 2
    assert {p.name for p in multi} == {"umap_local", "pca"}
    assert store.get_map_projection("does-not-exist") is None


def test_update_projection_metadata(tmp_path: Path):
    store = _store(tmp_path)
    proj = store.create_map_projection(
        model_name="discogs_multi", name="umap_local", method="umap"
    )

    updated = store.update_map_projection(
        proj.id,
        status="ready",
        source_embedding_count=500,
        projected_count=498,
        skipped_count=2,
        embedding_dim=400,
        diagnostics_json='{"runtime_seconds": 12.5}',
        completed_at="2026-07-11T00:00:00Z",
    )
    assert updated is not None
    assert updated.status == "ready"
    assert updated.source_embedding_count == 500
    assert updated.projected_count == 498
    assert updated.skipped_count == 2
    assert updated.embedding_dim == 400
    assert updated.diagnostics_json == '{"runtime_seconds": 12.5}'
    assert updated.completed_at == "2026-07-11T00:00:00Z"

    # Partial update must not clobber untouched columns.
    again = store.update_map_projection(proj.id, status="failed")
    assert again is not None
    assert again.status == "failed"
    assert again.projected_count == 498
    assert again.diagnostics_json == '{"runtime_seconds": 12.5}'


def test_points_round_trip_and_ordering(tmp_path: Path):
    store = _store(tmp_path)
    proj = store.create_map_projection(
        model_name="discogs_multi", name="umap_local", method="umap"
    )
    t1 = _make_track(store, tmp_path, "a")
    t2 = _make_track(store, tmp_path, "b")
    t3 = _make_track(store, tmp_path, "c")

    # Insert unordered; load must come back ordered by track_id.
    ids = np.array([t3, t1, t2], dtype=np.int64)
    coords = np.array([[3.0, 30.0], [1.0, 10.0], [2.0, 20.0]], dtype=np.float32)
    store.replace_map_projection_points(proj.id, ids, coords)

    assert store.count_map_projection_points(proj.id) == 3

    got_ids, got_xy = store.load_map_projection_points(proj.id)
    assert got_ids.tolist() == sorted([t1, t2, t3])
    assert got_xy.dtype == np.float32
    assert got_xy.shape == (3, 2)
    # Coordinates must follow their track_id after sorting.
    order = {t1: (1.0, 10.0), t2: (2.0, 20.0), t3: (3.0, 30.0)}
    for tid, (x, y) in zip(got_ids.tolist(), got_xy.tolist()):
        assert (x, y) == order[tid]


def test_replace_points_overwrites_previous(tmp_path: Path):
    store = _store(tmp_path)
    proj = store.create_map_projection(
        model_name="discogs_multi", name="umap_local", method="umap"
    )
    t1 = _make_track(store, tmp_path, "a")
    t2 = _make_track(store, tmp_path, "b")

    store.replace_map_projection_points(
        proj.id, [t1, t2], np.array([[0.0, 0.0], [1.0, 1.0]], dtype=np.float32)
    )
    store.replace_map_projection_points(
        proj.id, [t1], np.array([[9.0, 9.0]], dtype=np.float32)
    )

    ids, xy = store.load_map_projection_points(proj.id)
    assert ids.tolist() == [t1]
    assert xy.tolist() == [[9.0, 9.0]]


def test_load_empty_projection_returns_empty_arrays(tmp_path: Path):
    store = _store(tmp_path)
    proj = store.create_map_projection(
        model_name="discogs_multi", name="umap_local", method="umap"
    )
    ids, xy = store.load_map_projection_points(proj.id)
    assert ids.shape == (0,)
    assert xy.shape == (0, 2)
    assert store.count_map_projection_points(proj.id) == 0


def test_delete_projection_cascades_points(tmp_path: Path):
    store = _store(tmp_path)
    proj = store.create_map_projection(
        model_name="discogs_multi", name="umap_local", method="umap"
    )
    t1 = _make_track(store, tmp_path, "a")
    store.replace_map_projection_points(
        proj.id, [t1], np.array([[0.0, 0.0]], dtype=np.float32)
    )

    store.delete_map_projection(proj.id)

    assert store.get_map_projection(proj.id) is None
    assert store.count_map_projection_points(proj.id) == 0


def test_replace_points_rejects_bad_shape(tmp_path: Path):
    store = _store(tmp_path)
    proj = store.create_map_projection(
        model_name="discogs_multi", name="umap_local", method="umap"
    )
    t1 = _make_track(store, tmp_path, "a")

    with pytest.raises(ValueError):
        store.replace_map_projection_points(
            proj.id, [t1], np.array([[0.0, 0.0, 0.0]], dtype=np.float32)
        )
    with pytest.raises(ValueError):
        # length mismatch between ids and coords
        store.replace_map_projection_points(
            proj.id, [t1], np.array([[0.0, 0.0], [1.0, 1.0]], dtype=np.float32)
        )
