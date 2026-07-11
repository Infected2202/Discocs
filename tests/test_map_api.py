"""Map / embedding-atlas API tests.

Real round-trip against a tmp-db Store (via the same env wiring the other API
tests use). Projections are built with an injected fake projector so no UMAP is
imported. The neighbors endpoint is verified to delegate to the recommender,
never to the 2D coordinates.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
from fastapi.testclient import TestClient

from app.main import app
from app.models import FlowRegionTrack, SimilarTrack, TrackPrediction
from app.projection import build_projection
from app.scanner import ScannedTrack
from app.store import INITIALIZED_DB_PATHS, Store


def _init_store(tmp_path: Path, monkeypatch) -> Store:
    db_path = tmp_path / "app.db"
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    monkeypatch.setenv("DISCOCS_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_MODEL_DIR", str(tmp_path / "models"))
    for var in (
        "DISCOCS_NAVIDROME_URL",
        "DISCOCS_NAVIDROME_USER",
        "DISCOCS_NAVIDROME_PASSWORD",
        "DISCOCS_NAVIDROME_AUTH_MODE",
    ):
        monkeypatch.delenv(var, raising=False)
    store = Store(db_path)
    store.init()
    return store


def _add_track(store: Store, tmp_path: Path, name: str, *, artist: str, album: str, year: int) -> int:
    track_id, _ = store.upsert_track(
        ScannedTrack(
            path=(tmp_path / f"{name}.flac").resolve(),
            artist=artist,
            title=f"Title {name}",
            album=album,
            genre="Techno",
            year=year,
            duration=120.0,
            file_size=len(name) + year,
            mtime=1,
        )
    )
    return track_id


class _FakeReducer:
    """Deterministic 2D reducer used to avoid importing UMAP in tests."""

    def fit_transform(self, vectors: np.ndarray) -> np.ndarray:
        n = vectors.shape[0]
        return np.column_stack([np.arange(n), np.arange(n) * 10.0]).astype(np.float32)


def _fake_factory(method: str, params: dict, dim: int) -> _FakeReducer:
    return _FakeReducer()


def _seed_projection(store: Store, tmp_path: Path, *, model: str = "discogs_multi") -> tuple[str, list[int]]:
    ids = [
        _add_track(store, tmp_path, "a", artist="Alpha", album="One", year=2001),
        _add_track(store, tmp_path, "b", artist="Beta", album="Two", year=2002),
        _add_track(store, tmp_path, "c", artist="Alpha", album="One", year=2003),
    ]
    for i, tid in enumerate(ids):
        store.save_embedding(tid, model, np.array([0.1 * (i + 1), 0.2, 0.3, 0.4], dtype=np.float32))
    projection = build_projection(
        store, model_name=model, profile="umap_local", projector_factory=_fake_factory,
    )
    return projection.id, ids


# ---------------------------------------------------------------------------
# Projections: list / points / build
# ---------------------------------------------------------------------------

def test_list_projections_reports_ready_and_not_stale(tmp_path: Path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    projection_id, _ids = _seed_projection(store, tmp_path)
    client = TestClient(app)

    response = client.get("/api/map/projections?model=discogs_multi")

    assert response.status_code == 200
    projections = response.json()["projections"]
    assert len(projections) == 1
    proj = projections[0]
    assert proj["id"] == projection_id
    assert proj["status"] == "ready"
    assert proj["projected_count"] == 3
    assert proj["stale"] is False


def test_list_projections_flags_stale_after_new_embeddings(tmp_path: Path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    _seed_projection(store, tmp_path)
    # A new embedding lands after the projection was built → drift → stale.
    extra = _add_track(store, tmp_path, "d", artist="Gamma", album="Three", year=2004)
    store.save_embedding(extra, "discogs_multi", np.array([0.9, 0.2, 0.3, 0.4], dtype=np.float32))
    client = TestClient(app)

    proj = client.get("/api/map/projections").json()["projections"][0]
    assert proj["stale"] is True


def test_points_endpoint_returns_parallel_arrays(tmp_path: Path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    projection_id, ids = _seed_projection(store, tmp_path)
    client = TestClient(app)

    response = client.get(f"/api/map/projections/{projection_id}/points")

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 3
    assert data["track_ids"] == sorted(ids)
    assert len(data["x"]) == 3
    assert len(data["y"]) == 3
    # Deterministic reducer: y = index * 10 in track_id order.
    assert data["y"] == [0.0, 10.0, 20.0]


def test_points_unknown_projection_returns_404(tmp_path: Path, monkeypatch):
    _init_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.get("/api/map/projections/does-not-exist/points")

    assert response.status_code == 404


def test_projection_detail_returns_metadata(tmp_path: Path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    projection_id, _ids = _seed_projection(store, tmp_path)
    client = TestClient(app)

    response = client.get(f"/api/map/projections/{projection_id}")

    assert response.status_code == 200
    proj = response.json()["projection"]
    assert proj["id"] == projection_id
    assert proj["method"] == "umap"
    assert proj["embedding_dim"] == 4
    assert proj["diagnostics"] is not None


def test_projection_detail_unknown_returns_404(tmp_path: Path, monkeypatch):
    _init_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.get("/api/map/projections/nope")

    assert response.status_code == 404


def test_build_endpoint_enqueues_job(tmp_path: Path, monkeypatch):
    _init_store(tmp_path, monkeypatch)
    client = TestClient(app)

    with patch("app.api.map._build_map_projection_job") as job:
        response = client.post(
            "/api/map/projections",
            json={"model": "discogs_multi", "profile": "umap_local", "force": True},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["profile"] == "umap_local"
    assert body["force"] is True
    # Background task actually dispatched with the resolved arguments.
    job.assert_called_once()
    args = job.call_args.args
    assert args[0] == body["job_id"]
    assert args[1:] == ("discogs_multi", "umap_local", True)


def test_build_endpoint_rejects_unknown_profile(tmp_path: Path, monkeypatch):
    _init_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.post("/api/map/projections", json={"profile": "nope"})

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Dimensions + color
# ---------------------------------------------------------------------------

def test_dimensions_metadata_only_without_regions_or_mixes(tmp_path: Path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    projection_id, _ids = _seed_projection(store, tmp_path)
    client = TestClient(app)

    response = client.get(f"/api/map/dimensions?projection={projection_id}")

    assert response.status_code == 200
    keys = {d["key"] for d in response.json()["dimensions"]}
    assert keys == {"artist", "release", "genre", "year"}


def test_dimensions_include_region_and_mix_when_present(tmp_path: Path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    projection_id, ids = _seed_projection(store, tmp_path)
    profile = store.upsert_flow_profile("discogs_multi", "ready")
    region = store.upsert_flow_region(profile.id, 0, medoid_track_id=ids[0])
    store.replace_flow_region_tracks(
        region.id, [FlowRegionTrack(region.id, ids[0], "seed", 1.0, 0.0)]
    )
    store.save_generated_mix(
        mix_id="mix-1", title="Mix", mix_type="taste_region",
        items=[{"track_id": ids[1]}],
    )
    client = TestClient(app)

    keys = {d["key"] for d in client.get(f"/api/map/dimensions?projection={projection_id}").json()["dimensions"]}
    assert {"region", "mix"} <= keys


def test_color_artist_values_aligned_to_points(tmp_path: Path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    projection_id, ids = _seed_projection(store, tmp_path)
    client = TestClient(app)

    response = client.get(f"/api/map/projections/{projection_id}/color/artist")

    assert response.status_code == 200
    data = response.json()
    assert data["track_ids"] == sorted(ids)
    # ids sorted: a(Alpha), b(Beta), c(Alpha)
    assert data["values"] == ["Alpha", "Beta", "Alpha"]


def test_color_region_and_mix_values_aligned(tmp_path: Path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    projection_id, ids = _seed_projection(store, tmp_path)
    # ids[0] and ids[2] land in a region; ids[1] belongs to a mix.
    profile = store.upsert_flow_profile("discogs_multi", "ready")
    region = store.upsert_flow_region(profile.id, 3, medoid_track_id=ids[0])
    store.replace_flow_region_tracks(
        region.id,
        [
            FlowRegionTrack(region.id, ids[0], "seed", 1.0, 0.0),
            FlowRegionTrack(region.id, ids[2], "candidate", 0.4, 0.2),
        ],
    )
    store.save_generated_mix(
        mix_id="mix-1", title="Mix", mix_type="taste_region",
        items=[{"track_id": ids[1]}],
    )
    client = TestClient(app)

    region_values = client.get(f"/api/map/projections/{projection_id}/color/region").json()["values"]
    mix_values = client.get(f"/api/map/projections/{projection_id}/color/mix").json()["values"]

    # Points come back in sorted track_id order: ids[0], ids[1], ids[2].
    assert region_values == [3, None, 3]
    assert mix_values == [None, "mix-1", None]


def test_color_unknown_dimension_returns_400(tmp_path: Path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    projection_id, _ids = _seed_projection(store, tmp_path)
    client = TestClient(app)

    response = client.get(f"/api/map/projections/{projection_id}/color/loudness")

    assert response.status_code == 400


def test_labels_endpoint_returns_artist_and_title(tmp_path: Path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    projection_id, ids = _seed_projection(store, tmp_path)
    client = TestClient(app)

    data = client.get(f"/api/map/projections/{projection_id}/labels").json()

    # Aligned to sorted track_id order: a(Alpha), b(Beta), c(Alpha).
    assert data["track_ids"] == sorted(ids)
    assert data["artist"] == ["Alpha", "Beta", "Alpha"]
    assert data["title"] == ["Title a", "Title b", "Title c"]


def test_dimensions_include_genre_discogs400_only_when_classified(tmp_path: Path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    projection_id, ids = _seed_projection(store, tmp_path)
    client = TestClient(app)

    # No genre predictions yet → dimension absent.
    keys = {d["key"] for d in client.get(f"/api/map/dimensions?projection={projection_id}").json()["dimensions"]}
    assert "genre_discogs400" not in keys

    store.save_predictions(
        ids[0], "genre_discogs400",
        [TrackPrediction(label="Electronic---Techno", score=0.8, rank=1)],
    )
    keys = {d["key"] for d in client.get(f"/api/map/dimensions?projection={projection_id}").json()["dimensions"]}
    assert "genre_discogs400" in keys


def test_color_genre_discogs400_returns_top_genre_score_and_null(tmp_path: Path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    projection_id, ids = _seed_projection(store, tmp_path)
    # ids[0] and ids[2] classified; ids[1] left unclassified → null.
    store.save_predictions(
        ids[0], "genre_discogs400",
        [
            TrackPrediction(label="Electronic---Techno", score=0.8, rank=1),
            TrackPrediction(label="Electronic---House", score=0.5, rank=2),
        ],
    )
    store.save_predictions(
        ids[2], "genre_discogs400",
        [TrackPrediction(label="Rock---Punk", score=0.6, rank=1)],
    )
    client = TestClient(app)

    values = client.get(
        f"/api/map/projections/{projection_id}/color/genre_discogs400"
    ).json()["values"]

    # Sorted track_id order: ids[0], ids[1], ids[2]. Top-level genre before "---".
    assert values[0] == {"genre": "Electronic", "style": "Electronic---Techno", "score": 0.8}
    assert values[1] is None
    assert values[2] == {"genre": "Rock", "style": "Rock---Punk", "score": 0.6}


# ---------------------------------------------------------------------------
# Track inspection + neighbors
# ---------------------------------------------------------------------------

def test_track_inspection_includes_coords_and_membership(tmp_path: Path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    projection_id, ids = _seed_projection(store, tmp_path)
    store.save_generated_mix(
        mix_id="mix-1", title="Mix", mix_type="taste_region",
        items=[{"track_id": ids[0]}],
    )
    client = TestClient(app)

    response = client.get(f"/api/map/tracks/{ids[0]}?projection={projection_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["map_coords"] is not None
    assert data["mix_ids"] == ["mix-1"]
    assert data["track"]["id"] == ids[0]


def test_track_inspection_without_projection_omits_coords(tmp_path: Path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    _projection_id, ids = _seed_projection(store, tmp_path)
    client = TestClient(app)

    response = client.get(f"/api/map/tracks/{ids[0]}")

    assert response.status_code == 200
    data = response.json()
    assert data["projection_id"] is None
    assert data["map_coords"] is None
    assert data["region_index"] is None
    assert data["track"]["id"] == ids[0]


def test_track_inspection_unknown_track_returns_404(tmp_path: Path, monkeypatch):
    _init_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.get("/api/map/tracks/424242")

    assert response.status_code == 404


def test_neighbors_delegates_to_recommender_not_coordinates(tmp_path: Path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    _projection_id, ids = _seed_projection(store, tmp_path)
    neighbor_track = store.get_track(ids[1])
    fake_recommender = MagicMock()
    fake_recommender.similar.return_value = [
        SimilarTrack(track=neighbor_track, distance=0.2, similarity=0.8)
    ]
    client = TestClient(app)

    with patch("app.api.map.Recommender", return_value=fake_recommender) as recommender_cls:
        response = client.get(f"/api/map/tracks/{ids[0]}/neighbors?model=discogs_multi&k=5")

    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "hnsw"
    assert data["results"][0]["id"] == ids[1]
    # Delegation: the recommender was constructed and queried with the seed track.
    recommender_cls.assert_called_once()
    seed_arg = fake_recommender.similar.call_args.args[0]
    assert seed_arg.id == ids[0]


def test_neighbors_missing_index_returns_503(tmp_path: Path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    _projection_id, ids = _seed_projection(store, tmp_path)
    fake_recommender = MagicMock()
    fake_recommender.similar.side_effect = FileNotFoundError("index missing")
    client = TestClient(app)

    with patch("app.api.map.Recommender", return_value=fake_recommender):
        response = client.get(f"/api/map/tracks/{ids[0]}/neighbors")

    assert response.status_code == 503


def test_neighbors_unknown_track_returns_404(tmp_path: Path, monkeypatch):
    _init_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.get("/api/map/tracks/99999/neighbors")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Overlays
# ---------------------------------------------------------------------------

def test_mixes_overlay_lists_projected_members(tmp_path: Path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    projection_id, ids = _seed_projection(store, tmp_path)
    store.save_generated_mix(
        mix_id="mix-1", title="My Mix", mix_type="supermix",
        items=[{"track_id": ids[0]}, {"track_id": ids[2]}],
    )
    client = TestClient(app)

    response = client.get(f"/api/map/mixes?projection={projection_id}")

    assert response.status_code == 200
    mixes = response.json()["mixes"]
    assert len(mixes) == 1
    assert mixes[0]["id"] == "mix-1"
    assert sorted(mixes[0]["track_ids"]) == sorted([ids[0], ids[2]])
    assert mixes[0]["track_count"] == 2


def test_regions_overlay_lists_projected_members(tmp_path: Path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    projection_id, ids = _seed_projection(store, tmp_path)
    profile = store.upsert_flow_profile("discogs_multi", "ready")
    region = store.upsert_flow_region(profile.id, 0, medoid_track_id=ids[0], seed_count=1)
    store.replace_flow_region_tracks(
        region.id,
        [
            FlowRegionTrack(region.id, ids[0], "seed", 1.0, 0.0),
            FlowRegionTrack(region.id, ids[1], "candidate", 0.5, 0.3),
        ],
    )
    client = TestClient(app)

    response = client.get(f"/api/map/regions?projection={projection_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["model_key"] == "discogs_multi"
    assert len(body["regions"]) == 1
    assert sorted(body["regions"][0]["track_ids"]) == sorted([ids[0], ids[1]])


def test_regions_overlay_empty_without_profile(tmp_path: Path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    projection_id, _ids = _seed_projection(store, tmp_path)
    client = TestClient(app)

    response = client.get(f"/api/map/regions?projection={projection_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["profile"] is None
    assert body["regions"] == []
