from app.cli import worker_failure_retryable


import numpy as np
from typer.testing import CliRunner

from app.cli import cli
from app.scanner import ScannedTrack
from app.store import Store, TrackFeature


def test_worker_dependency_failures_are_not_retryable():
    assert not worker_failure_retryable(
        RuntimeError("essentia-tensorflow is required for rhythm extraction")
    )
    assert not worker_failure_retryable(ImportError("No module named 'essentia'"))


def test_worker_runtime_failures_remain_retryable():
    assert worker_failure_retryable(RuntimeError("temporary download failed"))


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
