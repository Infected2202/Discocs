from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.scanner import ScannedTrack
from app.store import INITIALIZED_DB_PATHS, Store
from app.timeline.artifacts import publish_artifact
from app.timeline.codec import EXTRACTOR, PACK_NAME, encode_timeline


def setup_track(tmp_path: Path, monkeypatch):
    db = tmp_path / "app.db"
    INITIALIZED_DB_PATHS.discard(db.resolve())
    monkeypatch.setenv("DISCOCS_DB_PATH", str(db))
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    store = Store(db)
    store.init()
    source = tmp_path / "track.flac"
    source.write_bytes(b"audio")
    track_id, _ = store.upsert_track(ScannedTrack(
        path=source.resolve(), artist="A", title="T", album="R",
        duration=5.0, file_size=5, mtime=10,
    ))
    return store, store.get_track(track_id)


def publish(store, track, root):
    base = {"minimum": [-.5], "maximum": [.5], "low": [.2], "mid": [.4], "high": [.6]}
    manifest, payload = encode_timeline(
        track_id=track.id, duration_seconds=5, sample_rate=44_100, base_bucket_samples=512,
        base=base, source={"path": track.path, "mtime": track.mtime, "file_size": track.file_size},
        extractor=EXTRACTOR,
        rhythm={"bpm": 120.0, "confidence": 0.8, "beats": [.5], "local_tempo": [120.0]},
    )
    publish_artifact(store, root, manifest, payload)
    return manifest, payload


def test_manifest_payload_and_batch_status(tmp_path: Path, monkeypatch):
    store, track = setup_track(tmp_path, monkeypatch)
    manifest, payload = publish(store, track, tmp_path / "timeline")
    client = TestClient(app)

    manifest_response = client.get(f"/api/v1/tracks/{track.id}/timeline/manifest")
    payload_response = client.get(f"/api/v1/tracks/{track.id}/timeline/payload")
    status = client.post("/api/v1/timeline/status", json={"track_ids": [track.id, 999]}).json()

    assert manifest_response.status_code == 200
    assert "path" not in manifest_response.json()["source"]
    assert payload_response.content == payload
    assert payload_response.headers["etag"] == f'"{manifest["payload"]["sha256"]}"'
    assert [item["status"] for item in status["items"]] == ["ready", "missing"]


def test_manifest_reports_stale_identity_and_job_validates_batch(tmp_path: Path, monkeypatch):
    store, track = setup_track(tmp_path, monkeypatch)
    publish(store, track, tmp_path / "timeline")
    store.upsert_track(ScannedTrack(
        path=Path(track.path), artist="A", title="T", album="R",
        duration=5.0, file_size=6, mtime=10,
    ))
    client = TestClient(app)

    assert client.get(f"/api/v1/tracks/{track.id}/timeline/manifest").status_code == 409
    status = client.post("/api/v1/timeline/status", json={"track_ids": [track.id]}).json()
    assert status["items"][0]["status"] == "stale"
    assert client.post("/api/v1/jobs/analyze-timeline", json={"track_ids": [999]}).status_code == 404


def test_missing_invalid_extractor_and_empty_job_paths(tmp_path: Path, monkeypatch):
    _store, track = setup_track(tmp_path, monkeypatch)
    client = TestClient(app)
    assert client.get(f"/api/v1/tracks/{track.id}/timeline/manifest").status_code == 404
    assert client.post("/api/v1/timeline/status", json={"track_ids": [track.id], "extractor": "future"}).status_code == 400
    assert client.post("/api/v1/jobs/analyze-timeline", json={"track_ids": [], "extractor": "future"}).status_code == 400
    accepted = client.post("/api/v1/jobs/analyze-timeline", json={"track_ids": []})
    assert accepted.status_code == 200
    assert accepted.json()["total"] == 0
    assert accepted.json()["extractor"] == EXTRACTOR


def test_batch_status_reports_durable_failure_and_corrupt_artifact(tmp_path: Path, monkeypatch):
    store, track = setup_track(tmp_path, monkeypatch)
    store.set_timeline_analysis_status(track.id, PACK_NAME, EXTRACTOR, "failed", error="worker failed")
    client = TestClient(app)
    failed = client.post("/api/v1/timeline/status", json={"track_ids": [track.id]}).json()["items"][0]
    assert failed == {"track_id": track.id, "status": "failed", "error": "worker failed"}
    _manifest, _payload = publish(store, track, tmp_path / "timeline")
    row = store.get_timeline_artifact(track.id, PACK_NAME, EXTRACTOR)
    Path(row["payload_path"]).write_bytes(b"broken")
    corrupt = client.post("/api/v1/timeline/status", json={"track_ids": [track.id]}).json()["items"][0]
    assert corrupt["status"] == "failed"
    assert "length mismatch" in corrupt["error"]
