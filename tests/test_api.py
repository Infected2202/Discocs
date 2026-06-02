from pathlib import Path
import base64
import sqlite3
import socket
from urllib.error import URLError

import numpy as np
from fastapi.testclient import TestClient

import app.main as main_module
from app.cli import worker_failure_retryable
from app.audio_features import AUDIO_FEATURE_EXTRACTOR
from app.head_pack import HeadOutput, Prediction
from app.main import app
from app.scanner import ScannedTrack
from app.store import INITIALIZED_DB_PATHS, Store, Track, TrackFeature


def init_api_store(tmp_path: Path, monkeypatch) -> Store:
    db_path = tmp_path / "app.db"
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    monkeypatch.setenv("DISCOCS_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_MODEL_DIR", str(tmp_path / "models"))
    store = Store(db_path)
    store.init()
    return store


def add_track(
    store: Store,
    path: Path,
    title: str = "Title",
    genre: str | None = None,
    year: int | None = None,
    artist: str = "Artist",
    album: str = "Album",
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        stat = path.stat()
        size = stat.st_size
        mtime = int(stat.st_mtime)
    else:
        size = 123
        mtime = 1
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=path,
            artist=artist,
            title=title,
            album=album,
            genre=genre,
            year=year,
            duration=123.0,
            file_size=size,
            mtime=mtime,
        )
    )
    return track_id


def test_health():
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_test_ui_loads():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "discocs" in response.text
    assert "Dashboard" in response.text
    assert "Library" in response.text
    assert "Browse" in response.text
    assert "Lost files" in response.text
    assert "Errored files" in response.text
    assert "Recommendations" in response.text
    assert "Evaluation" in response.text
    assert "Jobs" in response.text
    assert "Settings" in response.text
    assert "Analyze missing" in response.text
    assert "Download head models" in response.text
    assert "Analyze Discogs-EffNet heads" in response.text
    assert "Analyze audio features" in response.text
    assert "<details class=\"panel\" id=\"headPackDetails\">" in response.text
    assert "headPackModelTable" in response.text
    assert "model-table" in response.text
    assert "missingHeadPackTracks" in response.text
    assert "missingAudioFeatures" in response.text
    assert "missingFiles" in response.text
    assert "Check missing files" in response.text
    assert "Select all" in response.text
    assert "Remove selected" in response.text
    assert "Remove all" in response.text
    assert "lostFilesPage" in response.text
    assert "/lost-files" in response.text
    assert "Model files: ${ready}/${required} loaded" in response.text
    assert "modelFiles.filter(file => file.ready).length" in response.text
    assert "tracks analyzed`" not in response.text
    assert "<audio id=\"audioPlayer\"" in response.text
    assert "data-tooltip=\"Number of analyzer processes." in response.text
    assert "data-tooltip=\"TensorFlow/OMP threads per analyzer process." in response.text
    assert "discocs.settings.v1" in response.text
    assert "bindSettingsAutosave" in response.text
    assert "Analyze execution" in response.text
    assert "Local + remote" in response.text
    assert "Remote only" in response.text
    assert "Local only" in response.text
    assert "Remote worker" in response.text
    assert "workerCommand" in response.text
    assert "recs worker" in response.text
    assert "execution_mode: executionMode" in response.text
    assert "cancelJob" in response.text
    assert "parsedLimit && parsedLimit > 0 ? parsedLimit : null" in response.text
    assert "Add seed" in response.text
    assert "openAnalysis" in response.text
    assert "analysisModal" in response.text
    assert "icon-tablet" in response.text
    assert "Start session" in response.text
    assert "browse/facets" in response.text
    assert "facet-list" in response.text
    assert "compactFolder" in response.text


def test_worker_media_failures_are_not_retryable():
    terminal_errors = [
        RuntimeError("ffmpeg decoded no audio samples from /tmp/bad.mp3"),
        RuntimeError("ffmpeg failed to decode /tmp/bad.mp3 with exit code 1: Invalid data found when processing input"),
        RuntimeError("Output file #0 does not contain any stream"),
        ValueError("Embedding vector has zero norm"),
    ]

    for error in terminal_errors:
        assert worker_failure_retryable(error) is False


def test_stats_includes_pipeline_fields(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.get("/stats")

    assert response.status_code == 200
    data = response.json()
    assert "missing_embeddings" in data
    assert "model_exists" in data
    assert "index_exists" in data
    assert "head_pack_outputs" in data
    assert "missing_head_pack_tracks" in data
    assert "head_pack" in data
    assert "head_pack_expected_outputs" in data
    assert "head_pack_complete_tracks" in data
    assert "head_pack_missing_tracks" in data
    assert "audio_features_complete_tracks" in data
    assert "audio_features_missing_tracks" in data
    assert "missing_files" in data


def test_worker_claim_audio_and_submit_embedding(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    path = tmp_path / "track.flac"
    path.write_bytes(b"fake-audio")
    track_id = add_track(store, path)
    job = store.create_analysis_job("discogs_multi", None, local_executor_enabled=False)
    client = TestClient(app)

    response = client.post(
        "/workers/claim",
        json={"worker_id": "gpu-1", "models": ["discogs_multi"], "limit": 4},
    )

    assert response.status_code == 200
    tasks = response.json()["tasks"]
    assert len(tasks) == 1
    task = tasks[0]
    assert task["track_id"] == track_id
    assert task["job_id"] == job.id

    audio = client.get(task["audio_url"])
    assert audio.status_code == 200
    assert audio.content == b"fake-audio"

    vector = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    submit = client.post(
        "/workers/results",
        json={
            "worker_id": "gpu-1",
            "results": [
                {
                    "task_id": task["task_id"],
                    "track_id": track_id,
                    "model_name": "discogs_multi",
                    "dim": 3,
                    "dtype": "float32",
                    "vector_b64": base64.b64encode(vector.tobytes()).decode("ascii"),
                    "file_size": task["file_size"],
                    "mtime": task["mtime"],
                }
            ],
        },
    )

    assert submit.status_code == 200
    assert submit.json()["accepted"] == [task["task_id"]]
    assert np.allclose(store.load_embedding(track_id, "discogs_multi"), vector)
    assert store.get_analysis_job(job.id).done == 1


def test_worker_submit_audio_feature_result(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    path = tmp_path / "track.flac"
    path.write_bytes(b"fake-audio")
    track_id = add_track(store, path)
    job = store.create_analysis_job(
        AUDIO_FEATURE_EXTRACTOR,
        None,
        kind="analyze-audio-features",
        tracks=store.list_tracks_missing_features(AUDIO_FEATURE_EXTRACTOR),
        local_executor_enabled=False,
    )
    task = store.claim_analysis_tasks("gpu-1", [AUDIO_FEATURE_EXTRACTOR], limit=1)[0]
    client = TestClient(app)

    response = client.post(
        "/workers/results",
        json={
            "worker_id": "gpu-1",
            "feature_results": [
                {
                    "task_id": task.id,
                    "track_id": track_id,
                    "model_name": AUDIO_FEATURE_EXTRACTOR,
                    "file_size": task.file_size,
                    "mtime": task.mtime,
                    "features": [
                        {
                            "name": "bpm",
                            "value": 128.0,
                            "unit": "bpm",
                            "confidence": 0.9,
                            "extractor": AUDIO_FEATURE_EXTRACTOR,
                        }
                    ],
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["accepted"] == [task.id]
    features = store.load_features(track_id, AUDIO_FEATURE_EXTRACTOR)
    assert len(features) == 1
    assert features[0].name == "bpm"
    assert features[0].value == 128.0
    assert store.get_analysis_job(job.id).done == 1


def test_worker_submit_sqlite_lock_returns_retryable_error(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    path = tmp_path / "track.flac"
    path.write_bytes(b"fake-audio")
    track_id = add_track(store, path)
    store.create_analysis_job("discogs_multi", None)
    task = store.claim_analysis_tasks("gpu-1", ["discogs_multi"], limit=1)[0]
    vector = np.array([0.1, 0.2, 0.3], dtype=np.float32)

    def raise_locked(_item):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(main_module, "decode_worker_vector", raise_locked)
    client = TestClient(app)

    response = client.post(
        "/workers/results",
        json={
            "worker_id": "gpu-1",
            "results": [
                {
                    "task_id": task.id,
                    "track_id": track_id,
                    "model_name": "discogs_multi",
                    "dim": 3,
                    "dtype": "float32",
                    "vector_b64": base64.b64encode(vector.tobytes()).decode("ascii"),
                    "file_size": task.file_size,
                    "mtime": task.mtime,
                }
            ],
        },
    )

    assert response.status_code == 503
    unchanged = store.get_analysis_task(task.id)
    assert unchanged is not None
    assert unchanged.status == "leased"


def test_worker_submit_head_result(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    path = tmp_path / "track.flac"
    path.write_bytes(b"fake-audio")
    track_id = add_track(store, path)
    job = store.create_analysis_job(
        "discogs-effnet-heads",
        None,
        kind="analyze-heads",
        tracks=store.list_tracks_missing_head_pack(["genre_discogs400"]),
        local_executor_enabled=False,
    )
    task = store.claim_analysis_tasks("gpu-1", ["discogs-effnet-heads"], limit=1)[0]
    scores = np.array([0.8, 0.2], dtype=np.float32)
    client = TestClient(app)

    response = client.post(
        "/workers/results",
        json={
            "worker_id": "gpu-1",
            "head_results": [
                {
                    "task_id": task.id,
                    "track_id": track_id,
                    "model_name": "discogs-effnet-heads",
                    "file_size": task.file_size,
                    "mtime": task.mtime,
                    "outputs": [
                        {
                            "model_name": "genre_discogs400",
                            "dim": 2,
                            "dtype": "float32",
                            "aggregation": "mean_patches",
                            "scores_b64": base64.b64encode(scores.tobytes()).decode("ascii"),
                            "predictions": [
                                {"label": "Electronic---Techno", "score": 0.8, "rank": 1}
                            ],
                        }
                    ],
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["accepted"] == [task.id]
    output = store.load_model_output(track_id, "genre_discogs400")
    assert output is not None
    assert np.allclose(output.scores, scores)
    assert store.load_predictions(track_id, "genre_discogs400")[0].label == "Electronic---Techno"
    assert store.get_analysis_job(job.id).done == 1


def test_worker_submit_rejects_stale_result(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    path = tmp_path / "track.flac"
    path.write_bytes(b"fake-audio")
    track_id = add_track(store, path)
    job = store.create_analysis_job("discogs_multi", None, local_executor_enabled=False)
    task = store.claim_analysis_tasks("gpu-1", ["discogs_multi"], limit=1)[0]
    vector = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    client = TestClient(app)

    response = client.post(
        "/workers/results",
        json={
            "worker_id": "gpu-1",
            "results": [
                {
                    "task_id": task.id,
                    "track_id": track_id,
                    "model_name": "discogs_multi",
                    "dim": 3,
                    "dtype": "float32",
                    "vector_b64": base64.b64encode(vector.tobytes()).decode("ascii"),
                    "file_size": task.file_size + 1,
                    "mtime": task.mtime,
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["accepted"] == []
    assert response.json()["rejected"][0]["task_id"] == task.id
    assert store.load_embedding(track_id, "discogs_multi") is None
    assert store.get_analysis_job(job.id).failed == 1


def test_workers_endpoint_and_jobs_include_worker_status(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    path = tmp_path / "track.flac"
    path.write_bytes(b"fake-audio")
    add_track(store, path)
    store.create_analysis_job("discogs_multi", None, local_executor_enabled=False)
    client = TestClient(app)

    claim = client.post(
        "/workers/claim",
        json={"worker_id": "gpu-1", "models": ["discogs_multi"], "limit": 1},
    )
    assert claim.status_code == 200

    workers = client.get("/workers")
    assert workers.status_code == 200
    worker = workers.json()["workers"][0]
    assert worker["worker_id"] == "gpu-1"
    assert worker["models"] == ["discogs_multi"]
    assert worker["claimed_count"] == 1
    assert worker["stage"] == "claimed"

    jobs = client.get("/jobs")
    assert jobs.status_code == 200
    first_job = jobs.json()["jobs"][0]
    assert jobs.json()["workers"][0]["worker_id"] == "gpu-1"
    assert first_job["leased"] == 1
    assert first_job["oldest_lease"]["worker_id"] == "gpu-1"

    detail = client.get(f"/jobs/{first_job['id']}")
    assert detail.status_code == 200
    detail_data = detail.json()
    assert detail_data["job"]["id"] == first_job["id"]
    assert detail_data["tasks"][0]["lease_owner"] == "gpu-1"
    assert detail_data["tasks"][0]["status"] == "leased"


def test_cancel_analysis_job_endpoint_marks_zombie_cancelled(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    path = tmp_path / "track.flac"
    path.write_bytes(b"fake-audio")
    add_track(store, path)
    job = store.create_analysis_job("discogs_multi", None, local_executor_enabled=False)
    store.claim_analysis_tasks("gpu-1", ["discogs_multi"], limit=1)
    client = TestClient(app)

    response = client.post(
        f"/jobs/{job.id}/cancel",
        json={"reason": "test cancel"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    cancelled = store.get_analysis_job(job.id)
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.failed == 1
    jobs = client.get("/jobs").json()["jobs"]
    assert jobs[0]["status"] == "cancelled"


def test_head_pack_endpoint_returns_readiness_and_counts(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    path = tmp_path / "track.flac"
    path.write_bytes(b"fake")
    add_track(store, path)
    client = TestClient(app)

    response = client.get("/models/head-pack")

    assert response.status_code == 200
    data = response.json()
    assert data["pack"] == "discogs-effnet-heads"
    assert "missing_files" in data
    assert "files" in data
    assert "model_files" in data
    assert "per_head_output_counts" in data
    assert data["track_count"] == 1
    assert data["expected_outputs"] > 0
    filenames = {file["filename"] for file in data["model_files"]}
    assert "discogs_multi_embeddings-effnet-bs64-1.pb" in filenames
    assert "discogs-effnet-bs64-1.pb" in filenames


def test_track_analysis_endpoint_returns_outputs_predictions_and_features(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    path = tmp_path / "track.flac"
    path.write_bytes(b"fake")
    track_id = add_track(store, path, title="Track")
    store.save_model_output(
        track_id,
        "genre_discogs400",
        np.array([0.8, 0.2], dtype=np.float32),
        "mean_patches",
    )
    store.save_predictions(
        track_id,
        "genre_discogs400",
        [Prediction(label="Electronic---Techno", score=0.8, rank=1)],
    )
    store.save_features(
        track_id,
        [
            TrackFeature(
                name="bpm",
                value=128.0,
                unit="bpm",
                confidence=0.9,
                extractor=AUDIO_FEATURE_EXTRACTOR,
            )
        ],
    )
    client = TestClient(app)

    response = client.get(f"/tracks/{track_id}/analysis")

    assert response.status_code == 200
    data = response.json()
    assert data["track"]["id"] == track_id
    assert data["outputs"][0]["model_name"] == "genre_discogs400"
    assert data["outputs"][0]["dim"] == 2
    assert data["outputs"][0]["scores"] == [0.800000011920929, 0.20000000298023224]
    assert data["outputs"][0]["top_predictions"][0]["label"] == "Electronic---Techno"
    assert data["features"][0]["name"] == "bpm"


def test_jobs_list_endpoint():
    client = TestClient(app)

    response = client.get("/jobs")

    assert response.status_code == 200
    assert "jobs" in response.json()


def test_analyze_job_accepts_limit(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.post(
        "/jobs/analyze",
        json={"model": "discogs_multi", "limit": 1, "workers": 2},
    )

    assert response.status_code == 200
    assert response.json()["limit"] == 1
    assert response.json()["workers"] == 2
    assert response.json()["tf_threads"] == main_module.DEFAULT_ANALYZE_TF_THREADS


def test_analyze_job_defaults_to_benchmarked_runtime_when_available(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.post(
        "/jobs/analyze",
        json={"model": "discogs_multi", "limit": 1},
    )

    assert response.status_code == 200
    assert response.json()["workers"] == main_module.DEFAULT_ANALYZE_WORKERS
    assert response.json()["tf_threads"] == main_module.DEFAULT_ANALYZE_TF_THREADS


def test_analyze_job_accepts_tf_threads(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.post(
        "/jobs/analyze",
        json={"model": "discogs_multi", "limit": 1, "workers": 2, "tf_threads": 4},
    )

    assert response.status_code == 200
    assert response.json()["tf_threads"] == 4


def test_analyze_job_remote_mode_disables_local_executor(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("remote-only must not start local analyze executor")

    monkeypatch.setattr(main_module, "_analyze_job", fail_if_called)
    response = client.post(
        "/jobs/analyze",
        json={"model": "discogs_multi", "limit": 1, "execution_mode": "remote"},
    )

    assert response.status_code == 200
    assert response.json()["execution_mode"] == "remote"
    assert response.json()["local_executor_enabled"] is False


def test_analyze_audio_features_remote_mode_queues_worker_task(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    path = tmp_path / "track.flac"
    path.write_bytes(b"fake-audio")
    track_id = add_track(store, path)
    client = TestClient(app)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("remote-only must not start local audio feature executor")

    monkeypatch.setattr(main_module, "_analyze_audio_features_job", fail_if_called)
    response = client.post(
        "/jobs/analyze-audio-features",
        json={"execution_mode": "remote", "local_executor_enabled": False},
    )

    assert response.status_code == 200
    job = store.get_analysis_job(response.json()["job_id"])
    assert job is not None
    assert job.kind == "analyze-audio-features"
    assert job.model_name == AUDIO_FEATURE_EXTRACTOR
    assert job.queued == 1
    jobs = client.get("/jobs").json()["jobs"]
    assert "Waiting for worker supporting audio_features_v1" in jobs[0]["status_hint"]
    claim = client.post(
        "/workers/claim",
        json={"worker_id": "gpu-1", "models": [AUDIO_FEATURE_EXTRACTOR], "limit": 1},
    )
    assert claim.status_code == 200
    tasks = claim.json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["track_id"] == track_id


def test_analyze_heads_remote_mode_queues_worker_task(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    path = tmp_path / "track.flac"
    path.write_bytes(b"fake-audio")
    track_id = add_track(store, path)
    client = TestClient(app)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("remote-only must not start local head executor")

    monkeypatch.setattr(main_module, "_analyze_heads_job", fail_if_called)
    response = client.post(
        "/jobs/analyze-heads",
        json={"execution_mode": "remote", "local_executor_enabled": False},
    )

    assert response.status_code == 200
    job = store.get_analysis_job(response.json()["job_id"])
    assert job is not None
    assert job.kind == "analyze-heads"
    assert job.model_name == "discogs-effnet-heads"
    assert job.queued == 1
    jobs = client.get("/jobs").json()["jobs"]
    assert "Waiting for worker supporting discogs-effnet-heads" in jobs[0]["status_hint"]
    claim = client.post(
        "/workers/claim",
        json={"worker_id": "gpu-1", "models": ["discogs-effnet-heads"], "limit": 1},
    )
    assert claim.status_code == 200
    tasks = claim.json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["track_id"] == track_id


def test_analyze_heads_job_accepts_limit(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.post(
        "/jobs/analyze-heads",
        json={"limit": 1},
    )

    assert response.status_code == 200
    assert response.json()["limit"] == 1


def test_check_missing_files_job_accepts_request(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.post("/jobs/check-missing-files")

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


def test_download_head_pack_endpoint_reports_downloads(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)

    class FakeResult:
        def __init__(self, path, downloaded):
            self.path = path
            self.downloaded = downloaded

    monkeypatch.setattr(
        main_module,
        "download_head_pack_models",
        lambda settings: [FakeResult(settings.model_dir / "head.pb", True)],
    )
    monkeypatch.setattr(
        main_module,
        "head_pack_readiness",
        lambda settings: {"ready": True, "missing_files": []},
    )
    client = TestClient(app)

    response = client.post("/models/download-head-pack")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["downloaded"]
    assert response.json()["head_pack"]["ready"] is True


def test_download_head_models_job_reports_progress(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)

    class FakeResult:
        def __init__(self, path, downloaded):
            self.path = path
            self.downloaded = downloaded

    monkeypatch.setattr(
        main_module,
        "required_model_files",
        lambda: [("a.pb", "https://example.test/a.pb"), ("b.json", "https://example.test/b.json")],
    )

    def fake_download(settings, filename, source_url):
        return FakeResult(settings.model_dir / filename, filename == "a.pb")

    monkeypatch.setattr(main_module, "download_model_file", fake_download)
    job_id = main_module.create_job("download-head-models", "test")

    main_module._download_head_models_job(job_id)

    job = main_module.JOBS[job_id]
    assert job.status == "completed"
    assert job.done == 2
    assert "downloaded 1" in job.message


def test_download_head_models_job_reports_failed_filename(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    monkeypatch.setattr(
        main_module,
        "required_model_files",
        lambda: [("broken.pb", "https://example.test/broken.pb")],
    )

    def fake_download(settings, filename, source_url):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(main_module, "download_model_file", fake_download)
    job_id = main_module.create_job("download-head-models", "test")

    main_module._download_head_models_job(job_id)

    job = main_module.JOBS[job_id]
    assert job.status == "failed"
    assert "broken.pb" in job.message
    assert "RuntimeError" in job.message
    assert "network unavailable" in job.message
    assert "https://example.test/broken.pb" in job.error_detail
    assert "Traceback" in job.error_detail


def test_download_head_models_job_reports_empty_urlerror_reason(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    monkeypatch.setattr(
        main_module,
        "required_model_files",
        lambda: [("empty.pb", "https://example.test/empty.pb")],
    )

    def fake_download(settings, filename, source_url):
        raise URLError(None)

    monkeypatch.setattr(main_module, "download_model_file", fake_download)
    job_id = main_module.create_job("download-head-models", "test")

    main_module._download_head_models_job(job_id)

    job = main_module.JOBS[job_id]
    assert job.status == "failed"
    assert "empty.pb" in job.message
    assert "urllib.error.URLError" in job.message
    assert "repr: URLError(None)" in job.error_detail
    assert "args: (None,)" in job.error_detail
    assert "https://example.test/empty.pb" in job.error_detail


def test_download_head_models_job_explains_dns_failure(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    monkeypatch.setattr(
        main_module,
        "required_model_files",
        lambda: [("dns.pb", "https://essentia.upf.edu/models/dns.pb")],
    )

    def fake_download(settings, filename, source_url):
        raise URLError(socket.gaierror(-3, "Temporary failure in name resolution"))

    monkeypatch.setattr(main_module, "download_model_file", fake_download)
    job_id = main_module.create_job("download-head-models", "test")

    main_module._download_head_models_job(job_id)

    job = main_module.JOBS[job_id]
    assert job.status == "failed"
    assert "DNS lookup failed" in job.message
    assert "place the model files in models/ manually" in job.error_detail


def test_analyze_job_rejects_invalid_workers(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.post(
        "/jobs/analyze",
        json={"model": "discogs_multi", "limit": 1, "workers": 0},
    )

    assert response.status_code == 422


def test_analyze_job_rejects_zero_limit(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.post(
        "/jobs/analyze",
        json={"model": "discogs_multi", "limit": 0},
    )

    assert response.status_code == 422


def test_scan_job_fails_for_missing_music_directory(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    job_id = main_module.create_job("scan", "test")

    main_module._scan_job(job_id, tmp_path / "missing")

    job = main_module.JOBS[job_id]
    assert job.status == "failed"
    assert "Music directory not found" in job.message


def test_track_audio_returns_404_for_missing_track(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.get("/tracks/999/audio")

    assert response.status_code == 404
    assert response.json()["detail"] == "Track not found"


def test_track_audio_returns_clear_missing_file_error(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    track_id = add_track(store, tmp_path / "missing.flac")
    client = TestClient(app)

    response = client.get(f"/tracks/{track_id}/audio")

    assert response.status_code == 410
    assert "not mounted" in response.json()["detail"]
    assert store.get_track(track_id).missing_at is not None


def test_lost_files_list_paginates_and_delete_missing_tracks(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    present_path = tmp_path / "present.flac"
    present_path.write_bytes(b"fake")
    present_id = add_track(store, present_path, title="Present")
    missing_id = add_track(store, tmp_path / "missing.flac", title="Missing")
    second_missing_id = add_track(store, tmp_path / "second-missing.flac", title="Second Missing")
    store.mark_track_missing(missing_id, "2026-05-29T20:00:00+00:00")
    store.mark_track_missing(second_missing_id, "2026-05-29T20:01:00+00:00")
    client = TestClient(app)

    response = client.get("/lost-files?page=1&page_size=1")

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 2
    assert data["page"] == 1
    assert data["page_size"] == 1
    assert data["pages"] == 2
    assert len(data["results"]) == 1

    delete_response = client.request(
        "DELETE",
        "/lost-files",
        json={"track_ids": [missing_id, present_id]},
    )

    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] == 1
    assert store.get_track(missing_id) is None
    assert store.get_track(present_id) is not None

    delete_all_response = client.request(
        "DELETE",
        "/lost-files",
        json={"all_missing": True},
    )

    assert delete_all_response.status_code == 200
    assert delete_all_response.json()["deleted"] == 1
    assert store.get_track(second_missing_id) is None


def test_analysis_errors_list_paths_and_errors(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    bad_path = tmp_path / "bad.mp3"
    bad_path.write_bytes(b"not audio")
    track_id = add_track(store, bad_path, title="Broken")
    store.create_analysis_job("discogs_multi", None, max_attempts=1)
    task = store.claim_analysis_tasks("gpu-1", ["discogs_multi"], limit=1)[0]

    store.fail_analysis_task(
        task.id,
        error="ffmpeg decoded no audio samples",
        error_type="FfmpegDecodeError",
        stage="worker",
        worker_id="gpu-1",
        retryable=False,
    )

    client = TestClient(app)
    response = client.get("/analysis/errors")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    result = payload["results"][0]
    assert result["track_id"] == track_id
    assert result["path"] == str(bad_path)
    assert result["error"] == "ffmpeg decoded no audio samples"
    assert result["error_type"] == "FfmpegDecodeError"


def test_check_missing_files_job_marks_missing_and_available(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    present_path = tmp_path / "present.flac"
    present_path.write_bytes(b"fake")
    present_id = add_track(store, present_path, title="Present")
    missing_id = add_track(store, tmp_path / "missing.flac", title="Missing")
    store.mark_track_missing(present_id, "2026-05-29T20:00:00+00:00")
    job_id = main_module.create_job("check-missing-files", "test")

    main_module._check_missing_files_job(job_id)

    job = main_module.JOBS[job_id]
    assert job.status == "completed"
    assert job.done == 2
    assert "lost files 1" in job.message
    assert store.get_track(present_id).missing_at is None
    assert store.get_track(missing_id).missing_at is not None


def test_tracks_list_includes_has_embedding_and_filters(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    ready_path = tmp_path / "ready.flac"
    ready_path.write_bytes(b"fake")
    missing_path = tmp_path / "missing.flac"
    missing_path.write_bytes(b"fake")
    ready_id = add_track(store, ready_path, title="Ready")
    add_track(store, missing_path, title="Missing")
    store.save_embedding(ready_id, "discogs_multi", np.array([1.0, 0.0], dtype=np.float32))
    client = TestClient(app)

    all_response = client.get("/tracks?query=&limit=10&embedding_status=all&model=discogs_multi")
    ready_response = client.get("/tracks?embedding_status=ready&model=discogs_multi")
    missing_response = client.get("/tracks?embedding_status=missing&model=discogs_multi")

    assert all_response.status_code == 200
    assert {track["has_embedding"] for track in all_response.json()["results"]} == {True, False}
    assert [track["title"] for track in ready_response.json()["results"]] == ["Ready"]
    assert ready_response.json()["results"][0]["has_embedding"] is True
    assert [track["title"] for track in missing_response.json()["results"]] == ["Missing"]
    assert missing_response.json()["results"][0]["has_embedding"] is False


def test_tracks_list_filters_by_browser_metadata(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    techno_path = tmp_path / "warehouse" / "ready.flac"
    house_path = tmp_path / "club" / "missing.flac"
    for path in [techno_path, house_path]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake")
    ready_id = add_track(
        store,
        techno_path,
        title="Ready",
        genre="Techno",
        year=1998,
        artist="A",
        album="One",
    )
    add_track(
        store,
        house_path,
        title="Missing",
        genre="House",
        year=2001,
        artist="B",
        album="Two",
    )
    store.save_embedding(ready_id, "discogs_multi", np.array([1.0, 0.0], dtype=np.float32))
    client = TestClient(app)

    genre_response = client.get("/tracks?genre=Techno&model=discogs_multi")
    year_response = client.get("/tracks?year=2001&model=discogs_multi")
    folder_response = client.get(
        f"/tracks?folder={techno_path.parent}&embedding_status=ready&model=discogs_multi"
    )

    assert [track["title"] for track in genre_response.json()["results"]] == ["Ready"]
    assert genre_response.json()["results"][0]["genre"] == "Techno"
    assert genre_response.json()["results"][0]["year"] == 1998
    assert [track["title"] for track in year_response.json()["results"]] == ["Missing"]
    assert [track["title"] for track in folder_response.json()["results"]] == ["Ready"]


def test_browse_facets_include_counts_and_hide_missing_genres(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    tagged_path = tmp_path / "tagged" / "track.flac"
    untagged_path = tmp_path / "untagged" / "track.flac"
    for path in [tagged_path, untagged_path]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake")
    tagged_id = add_track(store, tagged_path, genre="Techno", year=1998, artist="A", album="One")
    add_track(store, untagged_path, title="No Genre")
    store.save_embedding(tagged_id, "discogs_multi", np.array([1.0, 0.0], dtype=np.float32))
    client = TestClient(app)

    response = client.get("/browse/facets?model=discogs_multi")

    assert response.status_code == 200
    data = response.json()
    assert data["genres"] == [{"value": "Techno", "count": 1}]
    assert {"value": 1998, "count": 1} in data["years"]
    assert any(item["value"] == str(tagged_path.parent) for item in data["folders"])


def test_similar_tracks_include_saved_rating(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    seed_path = tmp_path / "seed.flac"
    result_path = tmp_path / "result.flac"
    for path in [seed_path, result_path]:
        path.write_bytes(b"fake")
    seed_id = add_track(store, seed_path, title="Seed")
    result_id = add_track(store, result_path, title="Result", album="Two")
    store.save_embedding(seed_id, "discogs_multi", np.array([1.0, 0.0], dtype=np.float32))
    store.save_embedding(result_id, "discogs_multi", np.array([0.9, 0.1], dtype=np.float32))
    store.save_feedback(seed_id, result_id, "discogs_multi", 3)
    main_module.build_index(store, main_module.Settings.from_env(), "discogs_multi")
    client = TestClient(app)

    response = client.get(f"/tracks/{seed_id}/similar?model=discogs_multi&k=1")

    assert response.status_code == 200
    assert response.json()["results"][0]["rating"] == 3


def test_analyze_job_saves_successes_and_counts_failures_with_one_worker(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    first_path = tmp_path / "first.flac"
    second_path = tmp_path / "second.flac"
    fail_path = tmp_path / "fail.flac"
    for path in [first_path, second_path, fail_path]:
        path.write_bytes(b"fake")
    add_track(store, first_path, title="First")
    add_track(store, second_path, title="Second")
    add_track(store, fail_path, title="Fail")

    class FakeEmbedder:
        def __init__(self, settings, model):
            pass

        def extract_track_vector(self, path: Path):
            if path.stem == "fail":
                raise RuntimeError("decode failed")
            return np.array([1.0, 0.0], dtype=np.float32)

    monkeypatch.setattr(main_module, "DiscogsEffnetEmbedder", FakeEmbedder)
    result = main_module._extract_embedding_local(FakeEmbedder(None, "discogs_multi"), Track(
        id=99,
        path=str(fail_path),
        artist=None,
        title="Fail",
        album=None,
        duration=123.0,
        file_size=1,
        mtime=1,
    ))
    assert result.status == "failed"
    assert result.error_type == "RuntimeError"
    assert result.stage == "load_audio"
    assert "decode failed" in (result.traceback or "")
    job_id = main_module.create_job("analyze", "test")

    main_module._analyze_job(job_id, "discogs_multi", None, workers=1, tf_threads=1)

    assert store.count_embeddings("discogs_multi") == 2
    job = main_module.JOBS[job_id]
    assert job.status == "completed"
    assert job.done == 2
    assert job.failed == 1
    assert job.tracks_per_min is not None


def test_analyze_heads_job_saves_successes_and_counts_failures(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    first_path = tmp_path / "first.flac"
    fail_path = tmp_path / "fail.flac"
    for path in [first_path, fail_path]:
        path.write_bytes(b"fake")
    first_id = add_track(store, first_path, title="First")
    add_track(store, fail_path, title="Fail")

    class FakeHeadAnalyzer:
        def __init__(self, settings):
            pass

        def analyze_track(self, path: Path):
            if path.stem == "fail":
                raise RuntimeError("head inference failed")
            return [
                HeadOutput(
                    model_name="genre_discogs400",
                    scores=np.array([0.8, 0.4], dtype=np.float32),
                    aggregation="mean_patches",
                    predictions=[
                        Prediction(label="Electronic---Techno", score=0.8, rank=1),
                        Prediction(label="Electronic---House", score=0.4, rank=2),
                    ],
                ),
                HeadOutput(
                    model_name="danceability",
                    scores=np.array([0.2, 0.8], dtype=np.float32),
                    aggregation="mean_patches",
                    predictions=[
                        Prediction(label="danceability_1", score=0.8, rank=1),
                    ],
                ),
            ]

    monkeypatch.setattr(main_module, "DISCOGS_EFFNET_HEADS", [type("Head", (), {"id": "genre_discogs400"})(), type("Head", (), {"id": "danceability"})()])
    monkeypatch.setattr(main_module, "DiscogsEffnetHeadPackAnalyzer", FakeHeadAnalyzer)
    job_id = main_module.create_job("analyze-heads", "test")

    main_module._analyze_heads_job(job_id, None)

    assert store.count_predictions("genre_discogs400") == 1
    assert store.count_model_outputs("genre_discogs400") == 1
    assert store.count_model_outputs("danceability") == 1
    assert np.allclose(
        store.load_model_output(first_id, "genre_discogs400").scores,
        np.array([0.8, 0.4], dtype=np.float32),
    )
    assert store.load_predictions(first_id, "genre_discogs400")[0].label == "Electronic---Techno"
    job = main_module.JOBS[job_id]
    assert job.status == "completed"
    assert job.done == 1
    assert job.failed == 1


def test_analyze_audio_features_job_saves_successes_and_counts_failures(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    first_path = tmp_path / "first.flac"
    fail_path = tmp_path / "fail.flac"
    for path in [first_path, fail_path]:
        path.write_bytes(b"fake")
    first_id = add_track(store, first_path, title="First")
    add_track(store, fail_path, title="Fail")

    class FakeAudioFeatureAnalyzer:
        def analyze_track(self, path: Path):
            if path.stem == "fail":
                raise RuntimeError("feature extraction failed")
            return [
                TrackFeature(
                    name="bpm",
                    value=128.0,
                    unit="bpm",
                    confidence=0.9,
                    extractor=AUDIO_FEATURE_EXTRACTOR,
                )
            ]

    monkeypatch.setattr(main_module, "AudioFeatureAnalyzer", FakeAudioFeatureAnalyzer)
    job_id = main_module.create_job("analyze-audio-features", "test")

    main_module._analyze_audio_features_job(job_id, None)

    assert store.count_feature_tracks(AUDIO_FEATURE_EXTRACTOR) == 1
    assert store.load_features(first_id, AUDIO_FEATURE_EXTRACTOR)[0].name == "bpm"
    job = main_module.JOBS[job_id]
    assert job.status == "completed"
    assert job.done == 1
    assert job.failed == 1


def test_analyze_job_saves_successes_with_multiple_workers(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    first_path = tmp_path / "first.flac"
    second_path = tmp_path / "second.flac"
    for path in [first_path, second_path]:
        path.write_bytes(b"fake")
    add_track(store, first_path, title="First")
    add_track(store, second_path, title="Second")

    class FakeEmbedder:
        def __init__(self, settings, model):
            pass

        def extract_track_vector(self, path: Path):
            return np.array([1.0, 0.0], dtype=np.float32)

    class FakeFuture:
        def __init__(self, result):
            self._result = result

        def result(self):
            return self._result

    class FakeExecutor:
        max_workers = None
        initargs = None

        def __init__(self, max_workers, initializer, initargs, mp_context):
            self.max_workers = max_workers
            self.mp_context = mp_context
            initializer(*initargs)
            FakeExecutor.max_workers = max_workers
            FakeExecutor.initargs = initargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, fn, *args):
            return FakeFuture(fn(*args))

    monkeypatch.setattr(main_module, "DiscogsEffnetEmbedder", FakeEmbedder)
    monkeypatch.setattr(main_module, "ProcessPoolExecutor", FakeExecutor)
    monkeypatch.setattr(main_module, "as_completed", lambda futures: futures)
    job_id = main_module.create_job("analyze", "test")

    main_module._analyze_job(job_id, "discogs_multi", None, workers=2, tf_threads=4)

    assert FakeExecutor.max_workers == 2
    assert FakeExecutor.initargs[-1] == 4
    assert store.count_embeddings("discogs_multi") == 2
    job = main_module.JOBS[job_id]
    assert job.status == "completed"
    assert job.done == 2
    assert job.failed == 0
