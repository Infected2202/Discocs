from pathlib import Path

import pytest

from app.scanner import ScannedTrack
from app.store import Store
from app.timeline.artifacts import cleanup_artifact, cleanup_orphan_artifacts, load_valid_artifact, publish_artifact
from app.timeline.codec import EXTRACTOR, EXTRACTOR_V1, PACK_NAME, TimelineFormatError, encode_timeline
from app.timeline.jobs import run_timeline_job


def track_fixture(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    source = tmp_path / "track.flac"
    source.write_bytes(b"audio")
    track_id, _ = store.upsert_track(ScannedTrack(
        path=source.resolve(), artist="Artist", title="Track", album="Album",
        duration=10.0, file_size=5, mtime=11,
    ))
    return store, store.get_track(track_id)


def encoded(track, extractor=EXTRACTOR):
    values = {
        "minimum": [-0.5, -0.25], "maximum": [0.5, 0.25],
        "low": [0.8, 0.1], "mid": [0.1, 0.8], "high": [0.1, 0.1],
    }
    kwargs = {}
    if extractor == EXTRACTOR:
        kwargs["rhythm"] = {"bpm": 120.0, "confidence": 0.8, "beats": [0.5, 1.0], "local_tempo": [120.0, 120.0]}
    return encode_timeline(
        track_id=track.id, duration_seconds=10, sample_rate=44_100,
        base_bucket_samples=512, base=values,
        source={"path": track.path, "mtime": track.mtime, "file_size": track.file_size},
        extractor=extractor,
        **kwargs,
    )


def test_artifact_publish_round_trip_and_exact_source_invalidation(tmp_path: Path):
    store, track = track_fixture(tmp_path)
    root = tmp_path / "timeline"
    manifest, payload = encoded(track)

    publish_artifact(store, root, manifest, payload)
    loaded = load_valid_artifact(store, root, track, PACK_NAME, EXTRACTOR)

    assert loaded is not None
    assert store.timeline_artifact_counts(PACK_NAME, EXTRACTOR) == {
        "ready": 1, "missing": 0, "total": 1, "storage_bytes": len(payload),
    }
    assert loaded[0]["payload"]["sha256"] == manifest["payload"]["sha256"]
    assert loaded[1] == payload
    changed = ScannedTrack(
        path=Path(track.path), artist=track.artist, title=track.title, album=track.album,
        duration=track.duration, file_size=track.file_size + 1, mtime=track.mtime,
    )
    store.upsert_track(changed)
    with pytest.raises(TimelineFormatError, match="stale"):
        load_valid_artifact(store, root, store.get_track(track.id), PACK_NAME, EXTRACTOR)
    assert store.timeline_artifact_counts(PACK_NAME, EXTRACTOR) == {
        "ready": 0, "missing": 1, "total": 1, "storage_bytes": 0,
    }


def test_publish_rejects_corruption_and_cleanup_cannot_escape_root(tmp_path: Path):
    store, track = track_fixture(tmp_path)
    root = tmp_path / "timeline"
    manifest, payload = encoded(track)
    publish_artifact(store, root, manifest, payload)
    row = store.get_timeline_artifact(track.id, PACK_NAME, EXTRACTOR)
    Path(row["payload_path"]).write_bytes(payload[:-1])

    with pytest.raises(TimelineFormatError, match="payload"):
        load_valid_artifact(store, root, track, PACK_NAME, EXTRACTOR)

    store.upsert_timeline_artifact({
        "track_id": track.id, "pack_name": PACK_NAME, "extractor": EXTRACTOR,
        "schema_version": 1, "source_path": track.path, "source_mtime": track.mtime,
        "source_file_size": track.file_size, "manifest_path": str(tmp_path / "outside.json"),
        "payload_path": str(tmp_path / "outside.bin"), "payload_bytes": 0,
        "checksum_sha256": "0" * 64,
    })
    with pytest.raises(ValueError, match="escapes"):
        cleanup_artifact(store, root, track.id, PACK_NAME, EXTRACTOR)


def test_timeline_status_round_trip_and_track_delete_cascade(tmp_path: Path):
    store, track = track_fixture(tmp_path)
    store.set_timeline_analysis_status(track.id, PACK_NAME, EXTRACTOR, "failed", error="decode failed", job_id="job-1")
    assert store.get_timeline_analysis_states([track.id], PACK_NAME, EXTRACTOR)[track.id]["error"] == "decode failed"
    manifest, payload = encoded(track)
    publish_artifact(store, tmp_path / "timeline", manifest, payload)
    store.delete_tracks([track.id])
    assert store.get_timeline_artifact(track.id, PACK_NAME, EXTRACTOR) is None
    assert store.get_timeline_analysis_states([track.id], PACK_NAME, EXTRACTOR) == {}
    assert cleanup_orphan_artifacts(store, tmp_path / "timeline") == 2
    assert not (tmp_path / "timeline" / str(track.id)).exists()


def test_worker_failure_records_failure_without_publishing_partial_artifact(tmp_path: Path, monkeypatch):
    store, track = track_fixture(tmp_path)
    job = store.create_progress_job("analyze-timeline", EXTRACTOR, total=1)
    settings = type("Settings", (), {"data_dir": tmp_path})()
    monkeypatch.setattr("app.timeline.jobs.extract_timeline", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("decode failed")))

    run_timeline_job(store, settings, [track], job_id=job.id)

    state = store.get_timeline_analysis_states([track.id], PACK_NAME, EXTRACTOR)[track.id]
    assert state["status"] == "failed"
    assert state["error"] == "decode failed"
    assert store.get_timeline_artifact(track.id, PACK_NAME, EXTRACTOR) is None
    assert not (tmp_path / "timeline" / str(track.id)).exists()


def test_worker_success_publishes_and_reset_cleans_previous_artifact(tmp_path: Path, monkeypatch):
    store, track = track_fixture(tmp_path)
    settings = type("Settings", (), {"data_dir": tmp_path})()
    first_manifest, first_payload = encoded(track, EXTRACTOR_V1)
    publish_artifact(store, tmp_path / "timeline", first_manifest, first_payload)
    job = store.create_progress_job("analyze-timeline", EXTRACTOR, total=1)
    monkeypatch.setattr("app.timeline.jobs.extract_timeline", lambda *args, **kwargs: encoded(track))
    run_timeline_job(store, settings, [track], job_id=job.id, reset=True)
    state = store.get_timeline_analysis_states([track.id], PACK_NAME, EXTRACTOR)[track.id]
    assert state["status"] == "ready"
    assert load_valid_artifact(store, tmp_path / "timeline", track, PACK_NAME, EXTRACTOR) is not None
    assert store.get_timeline_artifact(track.id, PACK_NAME, EXTRACTOR_V1) is None
    assert store.get_analysis_job(job.id).status == "completed"


def test_publisher_validates_before_creating_files_and_needing_query_resumes(tmp_path: Path):
    store, track = track_fixture(tmp_path)
    manifest, payload = encoded(track)
    manifest["payload"]["sha256"] = "0" * 64
    with pytest.raises(TimelineFormatError, match="checksum"):
        publish_artifact(store, tmp_path / "timeline", manifest, payload)
    assert not (tmp_path / "timeline").exists()
    assert [item.id for item in store.list_tracks_needing_timeline(PACK_NAME, EXTRACTOR)] == [track.id]
