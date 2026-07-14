from app.cli import worker_failure_retryable

import logging

import numpy as np
import typer
from typer.testing import CliRunner

from app.cli import append_worker_failure, cli, format_worker_failure_traceback
from app.scanner import ScannedTrack
from app.store import Store, TrackFeature


def test_worker_dependency_failures_are_not_retryable():
    assert not worker_failure_retryable(
        RuntimeError("essentia-tensorflow is required for rhythm extraction")
    )
    assert not worker_failure_retryable(ImportError("No module named 'essentia'"))


def test_worker_runtime_failures_remain_retryable():
    assert worker_failure_retryable(RuntimeError("temporary download failed"))


def _raise_boom():
    raise ValueError("boom")


def test_format_worker_failure_traceback_includes_raise_site():
    try:
        _raise_boom()
    except ValueError as exc:
        tb_text = format_worker_failure_traceback(exc)

    assert "ValueError: boom" in tb_text
    assert "_raise_boom" in tb_text


def test_append_worker_failure_records_failure_and_logs_traceback(monkeypatch, caplog):
    echoed: list[str] = []
    monkeypatch.setattr(typer, "echo", lambda message="", **kwargs: echoed.append(message))

    failures: list[dict] = []
    with caplog.at_level(logging.ERROR, logger="discocs.analysis"):
        try:
            _raise_boom()
        except ValueError as exc:
            append_worker_failure(failures, "task-1", exc)

    assert failures == [
        {
            "task_id": "task-1",
            "error": "boom",
            "error_type": "ValueError",
            "stage": "worker",
            "retryable": True,
        }
    ]
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "_raise_boom" in logged
    assert "task_id=task-1" in logged
    assert any("_raise_boom" in message and "task-1" in message for message in echoed)


def test_db_rebuild_clean_drops_track_features_by_default(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    output_path = tmp_path / "rebuilt.db"
    store = Store(db_path)
    store.init()
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=(tmp_path / "track.flac").resolve(),
            artist="Artist",
            title="Title",
            album="Album",
            duration=123.0,
            file_size=100,
            mtime=1,
        )
    )
    store.save_embedding(track_id, "discogs_multi", np.array([1.0, 0.0], dtype=np.float32))
    store.save_features(
        track_id,
        [TrackFeature(name="bpm", value=128.0, extractor="audio_features_v1")],
    )
    monkeypatch.setenv("DISCOCS_DB_PATH", str(db_path))

    result = CliRunner().invoke(cli, ["db-rebuild-clean", "--output", str(output_path)])

    assert result.exit_code == 0, result.output
    rebuilt = Store(output_path)
    rebuilt.init()
    assert rebuilt.count_tracks() == 1
    assert rebuilt.count_embeddings("discogs_multi") == 1
    assert rebuilt.count_feature_tracks("audio_features_v1") == 0
