"""External-seed radio: POST /api/v1/similar/by-audio and its analysis service.

The load-bearing property here is that an external seed leaves no trace: no
embeddings, no tracks, no index writes. Several tests below exist only to fail
if that ever stops being true.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

import app.api.external as api_external_module
import app.main as main_module
import app.services.external_audio as external_audio_module
from app.main import app
from app.scanner import ScannedTrack
from app.services.external_audio import (
    ANALYSIS_WINDOW_SECONDS,
    LONG_AUDIO_THRESHOLD_SECONDS,
    AudioProbe,
    ExternalAudioError,
    analysis_window,
    extract_query_vector,
    reset_vector_cache,
)
from app.store import INITIALIZED_DB_PATHS, Store


MODEL = "discogs_multi"
AUDIO_BODY = b"ID3fake-audio-bytes"


class FakeEmbedder:
    """Stands in for Discogs-EffNet: no TensorFlow, no Essentia, no audio."""

    def __init__(self, vector: np.ndarray) -> None:
        self.vector = vector
        self.calls: list[Path] = []

    def extract_track_vector(self, path: Path) -> np.ndarray:
        self.calls.append(path)
        return self.vector


@pytest.fixture(autouse=True)
def _clear_vector_cache():
    reset_vector_cache()
    yield
    reset_vector_cache()


def init_store(tmp_path: Path, monkeypatch) -> Store:
    db_path = tmp_path / "app.db"
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    monkeypatch.setenv("DISCOCS_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_MODEL_DIR", str(tmp_path / "models"))
    store = Store(db_path)
    store.init()
    return store


def add_navidrome_track(store: Store, item_id: str, title: str, album: str = "Album") -> int:
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=f"navidrome://{item_id}",  # type: ignore[arg-type]
            artist=f"Artist {item_id}",
            title=title,
            album=album,
            duration=123.0,
            file_size=100,
            mtime=0,
        )
    )
    return track_id


def stub_embedder(monkeypatch, vector: np.ndarray) -> FakeEmbedder:
    embedder = FakeEmbedder(vector)
    monkeypatch.setattr(
        external_audio_module,
        "create_track_embedder",
        lambda settings, model_name, **kwargs: embedder,
    )
    return embedder


def stub_probe(monkeypatch, duration: float | None, has_audio_stream: bool = True) -> None:
    monkeypatch.setattr(
        external_audio_module,
        "probe_audio",
        lambda path: AudioProbe(has_audio_stream=has_audio_stream, duration_seconds=duration),
    )


def build_catalog(store: Store) -> tuple[int, int]:
    near_id = add_navidrome_track(store, "near", "Near", album="One")
    far_id = add_navidrome_track(store, "far", "Far", album="Two")
    store.upsert_external_track("navidrome", "near", near_id)
    store.upsert_external_track("navidrome", "far", far_id)
    store.save_embedding(near_id, MODEL, np.array([1.0, 0.0], dtype=np.float32))
    store.save_embedding(far_id, MODEL, np.array([0.0, 1.0], dtype=np.float32))
    main_module.build_index(store, main_module.Settings.from_env(), MODEL)
    return near_id, far_id


# ---------------------------------------------------------------------------
# Analysis window
# ---------------------------------------------------------------------------

def test_analysis_window_is_skipped_for_normal_tracks():
    assert analysis_window(None) is None
    assert analysis_window(240.0) is None
    assert analysis_window(LONG_AUDIO_THRESHOLD_SECONDS) is None


def test_analysis_window_takes_the_middle_of_a_long_set():
    duration = 7200.0

    start, seconds = analysis_window(duration)

    assert seconds == ANALYSIS_WINDOW_SECONDS
    assert start == (duration - ANALYSIS_WINDOW_SECONDS) / 2
    assert start + seconds < duration


def test_long_audio_is_trimmed_before_embedding(tmp_path: Path, monkeypatch):
    settings = _settings_for(tmp_path, monkeypatch)
    audio = tmp_path / "set.mp3"
    audio.write_bytes(AUDIO_BODY)
    stub_probe(monkeypatch, duration=7200.0)
    embedder = stub_embedder(monkeypatch, np.array([1.0, 0.0], dtype=np.float32))
    trimmed = tmp_path / "window.flac"
    trimmed.write_bytes(b"window")
    calls: list[tuple[float, float]] = []

    def fake_trim(path: Path, start: float, seconds: float, work_dir: Path) -> Path:
        calls.append((start, seconds))
        return trimmed

    monkeypatch.setattr(external_audio_module, "trim_window", fake_trim)

    result = extract_query_vector(settings, MODEL, audio, tmp_path)

    assert calls == [((7200.0 - ANALYSIS_WINDOW_SECONDS) / 2, ANALYSIS_WINDOW_SECONDS)]
    assert embedder.calls == [trimmed]
    assert result.analyzed_seconds == ANALYSIS_WINDOW_SECONDS
    assert result.analysis_offset_seconds == (7200.0 - ANALYSIS_WINDOW_SECONDS) / 2
    assert not trimmed.exists()


def test_short_audio_is_embedded_whole(tmp_path: Path, monkeypatch):
    settings = _settings_for(tmp_path, monkeypatch)
    audio = tmp_path / "track.mp3"
    audio.write_bytes(AUDIO_BODY)
    stub_probe(monkeypatch, duration=200.0)
    embedder = stub_embedder(monkeypatch, np.array([1.0, 0.0], dtype=np.float32))
    monkeypatch.setattr(
        external_audio_module,
        "trim_window",
        lambda *args, **kwargs: pytest.fail("short audio must not be trimmed"),
    )

    result = extract_query_vector(settings, MODEL, audio, tmp_path)

    assert embedder.calls == [audio]
    assert result.analysis_offset_seconds == 0.0
    assert result.analyzed_seconds == 200.0


# ---------------------------------------------------------------------------
# Probing and caching
# ---------------------------------------------------------------------------

def test_probe_audio_returns_none_when_ffprobe_is_unavailable(tmp_path: Path, monkeypatch):
    audio = tmp_path / "track.mp3"
    audio.write_bytes(AUDIO_BODY)

    def raise_missing(*args, **kwargs):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr(subprocess, "run", raise_missing)

    assert external_audio_module.probe_audio(audio) is None


def test_file_without_audio_stream_is_rejected(tmp_path: Path, monkeypatch):
    settings = _settings_for(tmp_path, monkeypatch)
    audio = tmp_path / "cover.png"
    audio.write_bytes(b"not audio")
    stub_probe(monkeypatch, duration=None, has_audio_stream=False)
    stub_embedder(monkeypatch, np.array([1.0, 0.0], dtype=np.float32))

    with pytest.raises(ExternalAudioError):
        extract_query_vector(settings, MODEL, audio, tmp_path)


def test_identical_audio_is_embedded_once(tmp_path: Path, monkeypatch):
    settings = _settings_for(tmp_path, monkeypatch)
    first = tmp_path / "a.mp3"
    second = tmp_path / "b.mp3"
    first.write_bytes(AUDIO_BODY)
    second.write_bytes(AUDIO_BODY)
    stub_probe(monkeypatch, duration=200.0)
    embedder = stub_embedder(monkeypatch, np.array([1.0, 0.0], dtype=np.float32))

    first_result = extract_query_vector(settings, MODEL, first, tmp_path)
    second_result = extract_query_vector(settings, MODEL, second, tmp_path)

    assert len(embedder.calls) == 1
    assert first_result.cached is False
    assert second_result.cached is True
    assert np.array_equal(first_result.vector, second_result.vector)


def test_different_audio_is_embedded_again(tmp_path: Path, monkeypatch):
    settings = _settings_for(tmp_path, monkeypatch)
    first = tmp_path / "a.mp3"
    second = tmp_path / "b.mp3"
    first.write_bytes(AUDIO_BODY)
    second.write_bytes(AUDIO_BODY + b"-other")
    stub_probe(monkeypatch, duration=200.0)
    embedder = stub_embedder(monkeypatch, np.array([1.0, 0.0], dtype=np.float32))

    extract_query_vector(settings, MODEL, first, tmp_path)
    extract_query_vector(settings, MODEL, second, tmp_path)

    assert len(embedder.calls) == 2


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_similar_by_audio_returns_navidrome_items(tmp_path: Path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    near_id, _far_id = build_catalog(store)
    stub_probe(monkeypatch, duration=200.0)
    stub_embedder(monkeypatch, np.array([1.0, 0.0], dtype=np.float32))
    client = TestClient(app)

    response = client.post("/api/v1/similar/by-audio", content=AUDIO_BODY)

    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "external_audio"
    assert data["model"] == MODEL
    assert data["vector_cached"] is False
    assert data["duration_seconds"] == 200.0
    assert data["results"][0]["item_id"] == "near"
    assert data["results"][0]["track_id"] == near_id
    assert data["results"][0]["similarity"] > 0


def test_similar_by_audio_never_writes_to_the_catalog(tmp_path: Path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    build_catalog(store)
    stub_probe(monkeypatch, duration=200.0)
    stub_embedder(monkeypatch, np.array([1.0, 0.0], dtype=np.float32))
    index_path = main_module.Settings.from_env().index_path(MODEL)
    embeddings_before = store.count_embeddings(MODEL)
    tracks_before = store.count_tracks()
    index_before = index_path.stat().st_size, index_path.stat().st_mtime_ns
    client = TestClient(app)

    response = client.post("/api/v1/similar/by-audio", content=AUDIO_BODY)

    assert response.status_code == 200
    assert store.count_embeddings(MODEL) == embeddings_before
    assert store.count_tracks() == tracks_before
    assert (index_path.stat().st_size, index_path.stat().st_mtime_ns) == index_before


def test_similar_by_audio_leaves_no_temp_file(tmp_path: Path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    build_catalog(store)
    stub_probe(monkeypatch, duration=200.0)
    stub_embedder(monkeypatch, np.array([1.0, 0.0], dtype=np.float32))
    client = TestClient(app)

    response = client.post("/api/v1/similar/by-audio", content=AUDIO_BODY)

    assert response.status_code == 200
    work_dir = tmp_path / "tmp" / "external"
    assert list(work_dir.iterdir()) == []


def test_similar_by_audio_skips_results_without_navidrome_id(tmp_path: Path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    mapped_id = add_navidrome_track(store, "mapped", "Mapped", album="One")
    unmapped_id = add_navidrome_track(store, "unmapped", "Unmapped", album="Two")
    store.upsert_external_track("navidrome", "mapped", mapped_id)
    store.save_embedding(mapped_id, MODEL, np.array([0.9, 0.1], dtype=np.float32))
    store.save_embedding(unmapped_id, MODEL, np.array([1.0, 0.0], dtype=np.float32))
    main_module.build_index(store, main_module.Settings.from_env(), MODEL)
    stub_probe(monkeypatch, duration=200.0)
    stub_embedder(monkeypatch, np.array([1.0, 0.0], dtype=np.float32))
    client = TestClient(app)

    response = client.post("/api/v1/similar/by-audio", content=AUDIO_BODY)

    assert response.status_code == 200
    data = response.json()
    assert [item["item_id"] for item in data["results"]] == ["mapped"]
    assert data["skipped_without_external_id"] == 1


def test_similar_by_audio_rejects_empty_body(tmp_path: Path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    build_catalog(store)
    client = TestClient(app)

    response = client.post("/api/v1/similar/by-audio", content=b"")

    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_similar_by_audio_rejects_oversized_body(tmp_path: Path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    build_catalog(store)
    monkeypatch.setenv("DISCOCS_EXTERNAL_AUDIO_MAX_MB", "1")
    client = TestClient(app)

    response = client.post("/api/v1/similar/by-audio", content=b"x" * (2 * 1024 * 1024))

    assert response.status_code == 413


def test_similar_by_audio_rejects_undecodable_audio(tmp_path: Path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    build_catalog(store)
    stub_probe(monkeypatch, duration=None, has_audio_stream=False)
    client = TestClient(app)

    response = client.post("/api/v1/similar/by-audio", content=AUDIO_BODY)

    assert response.status_code == 400


def test_similar_by_audio_reports_missing_index(tmp_path: Path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    track_id = add_navidrome_track(store, "near", "Near")
    store.upsert_external_track("navidrome", "near", track_id)
    store.save_embedding(track_id, MODEL, np.array([1.0, 0.0], dtype=np.float32))
    stub_probe(monkeypatch, duration=200.0)
    stub_embedder(monkeypatch, np.array([1.0, 0.0], dtype=np.float32))
    client = TestClient(app)

    response = client.post("/api/v1/similar/by-audio", content=AUDIO_BODY)

    assert response.status_code == 503


def test_similar_by_audio_reuses_the_vector_cache_across_requests(tmp_path: Path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    build_catalog(store)
    stub_probe(monkeypatch, duration=200.0)
    embedder = stub_embedder(monkeypatch, np.array([1.0, 0.0], dtype=np.float32))
    client = TestClient(app)

    client.post("/api/v1/similar/by-audio", content=AUDIO_BODY)
    second = client.post("/api/v1/similar/by-audio", content=AUDIO_BODY)

    assert len(embedder.calls) == 1
    assert second.json()["vector_cached"] is True


def test_similar_by_audio_openapi_uses_annotated_response_model():
    client = TestClient(app)

    response_schema = client.get("/openapi.json").json()["paths"]["/api/v1/similar/by-audio"]["post"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]

    assert response_schema == {"$ref": "#/components/schemas/ExternalAudioSimilarResponse"}


def test_max_upload_bytes_falls_back_on_garbage(monkeypatch):
    monkeypatch.setenv("DISCOCS_EXTERNAL_AUDIO_MAX_MB", "not-a-number")

    assert api_external_module.max_upload_bytes() == (
        api_external_module.DEFAULT_MAX_UPLOAD_MB * 1024 * 1024
    )


def _settings_for(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_MODEL_DIR", str(tmp_path / "models"))
    return main_module.Settings.from_env()
