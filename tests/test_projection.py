from pathlib import Path

import numpy as np
import pytest

from app.models import utc_now
from app.projection import build_projection
from app.scanner import ScannedTrack
from app.store import Store


class FakeReducer:
    """Deterministic 2D reducer; records each fit_transform via a shared list."""

    def __init__(self, calls: list[int]):
        self._calls = calls

    def fit_transform(self, vectors: np.ndarray) -> np.ndarray:
        self._calls.append(1)
        n = vectors.shape[0]
        return np.column_stack([np.arange(n), np.arange(n) * 2.0]).astype(np.float32)


def _factory(calls: list[int]):
    def factory(method: str, params: dict, dim: int) -> FakeReducer:
        return FakeReducer(calls)

    return factory


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "app.db")
    store.init()
    return store


def _seed(store: Store, tmp_path: Path, n: int, *, model: str = "discogs_multi", dim: int = 4) -> list[int]:
    ids: list[int] = []
    for i in range(n):
        scanned = ScannedTrack(
            path=(tmp_path / f"t{i}.flac").resolve(),
            artist=f"Artist {i}",
            title=f"Title {i}",
            album="Album",
            genre="Techno",
            year=2000,
            duration=100.0,
            file_size=i + 1,
            mtime=1,
        )
        track_id, _ = store.upsert_track(scanned)
        vector = np.random.RandomState(i).rand(dim).astype(np.float32)
        store.save_embedding(track_id, model, vector)
        ids.append(track_id)
    return ids


def _mark_missing(store: Store, track_id: int) -> None:
    with store.connect() as conn:
        conn.execute("UPDATE tracks SET missing_at = ? WHERE id = ?", (utc_now(), track_id))


def test_build_projection_persists_points(tmp_path: Path):
    store = _store(tmp_path)
    ids = _seed(store, tmp_path, 5)
    calls: list[int] = []

    projection = build_projection(
        store, model_name="discogs_multi", profile="umap_local",
        projector_factory=_factory(calls),
    )

    assert projection.status == "ready"
    assert projection.method == "umap"
    assert projection.metric == "cosine"
    assert projection.projected_count == 5
    assert projection.skipped_count == 0
    assert projection.source_embedding_count == 5
    assert projection.embedding_dim == 4
    assert projection.completed_at is not None
    assert len(calls) == 1

    got_ids, got_xy = store.load_map_projection_points(projection.id)
    assert sorted(got_ids.tolist()) == sorted(ids)
    assert got_xy.shape == (5, 2)


def test_build_skips_missing_tracks(tmp_path: Path):
    store = _store(tmp_path)
    ids = _seed(store, tmp_path, 5)
    _mark_missing(store, ids[0])
    _mark_missing(store, ids[1])

    projection = build_projection(
        store, model_name="discogs_multi", projector_factory=_factory([]),
    )

    assert projection.projected_count == 3
    assert projection.skipped_count == 2
    assert projection.source_embedding_count == 5

    point_ids, _ = store.load_map_projection_points(projection.id)
    assert set(point_ids.tolist()) == set(ids[2:])


def test_build_empty_source_raises_and_marks_failed(tmp_path: Path):
    store = _store(tmp_path)  # no embeddings at all

    with pytest.raises(ValueError):
        build_projection(store, model_name="discogs_multi", projector_factory=_factory([]))

    projection = store.find_map_projection("discogs_multi", "umap_local")
    assert projection is not None
    assert projection.status == "failed"
    assert projection.projected_count == 0


def test_ready_projection_not_recomputed_without_force(tmp_path: Path):
    store = _store(tmp_path)
    _seed(store, tmp_path, 3)
    calls: list[int] = []

    first = build_projection(
        store, model_name="discogs_multi", projector_factory=_factory(calls)
    )
    assert len(calls) == 1

    again = build_projection(
        store, model_name="discogs_multi", projector_factory=_factory(calls)
    )
    assert again.id == first.id
    assert len(calls) == 1  # not recomputed

    forced = build_projection(
        store, model_name="discogs_multi", force=True, projector_factory=_factory(calls)
    )
    assert forced.id == first.id
    assert len(calls) == 2  # rebuilt in place


def test_unknown_profile_raises(tmp_path: Path):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        build_projection(store, model_name="discogs_multi", profile="does-not-exist")


def test_bad_projector_shape_marks_failed(tmp_path: Path):
    store = _store(tmp_path)
    _seed(store, tmp_path, 4)

    class BadReducer:
        def fit_transform(self, vectors: np.ndarray) -> np.ndarray:
            return np.zeros((vectors.shape[0], 3), dtype=np.float32)  # 3 cols, not 2

    def bad_factory(method, params, dim):
        return BadReducer()

    with pytest.raises(ValueError):
        build_projection(store, model_name="discogs_multi", projector_factory=bad_factory)

    projection = store.find_map_projection("discogs_multi", "umap_local")
    assert projection is not None
    assert projection.status == "failed"


def test_pca_profile_metadata(tmp_path: Path):
    store = _store(tmp_path)
    _seed(store, tmp_path, 3)

    projection = build_projection(
        store, model_name="discogs_multi", profile="pca", projector_factory=_factory([])
    )
    assert projection.method == "pca"
    assert projection.metric == "euclidean"
    assert projection.status == "ready"
