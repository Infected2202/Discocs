from pathlib import Path

import numpy as np

from app.scanner import ScannedTrack
from app.store import Store, TrackFeature, TrackPrediction


def test_store_upsert_and_embedding_round_trip(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    scanned = ScannedTrack(
        path=(tmp_path / "track.flac").resolve(),
        artist="Artist",
        title="Title",
        album="Album",
        genre="Techno",
        year=1998,
        duration=123.0,
        file_size=100,
        mtime=1,
    )

    track_id, changed = store.upsert_track(scanned)
    assert changed is True

    same_id, changed = store.upsert_track(scanned)
    assert same_id == track_id
    assert changed is False

    vector = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    store.save_embedding(track_id, "discogs_multi", vector)

    assert store.count_tracks() == 1
    assert store.count_embeddings("discogs_multi") == 1
    assert np.allclose(store.load_embedding(track_id, "discogs_multi"), vector)
    assert store.get_track(track_id).genre == "Techno"
    assert store.get_track(track_id).year == 1998

    changed_scan = ScannedTrack(
        path=scanned.path,
        artist="Artist",
        title="Title",
        album="Album",
        genre="Techno",
        year=1999,
        duration=123.0,
        file_size=101,
        mtime=2,
    )
    same_id, changed = store.upsert_track(changed_scan)

    assert same_id == track_id
    assert changed is True
    assert store.load_embedding(track_id, "discogs_multi") is None


def test_metadata_rescan_without_file_change_keeps_embedding(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    path = (tmp_path / "track.flac").resolve()
    original = ScannedTrack(
        path=path,
        artist="Artist",
        title="Title",
        album="Album",
        genre=None,
        year=None,
        duration=123.0,
        file_size=100,
        mtime=1,
    )
    track_id, _changed = store.upsert_track(original)
    vector = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    store.save_embedding(track_id, "discogs_multi", vector)

    refreshed = ScannedTrack(
        path=path,
        artist="Artist",
        title="Title",
        album="Album",
        genre="House",
        year=2001,
        duration=123.0,
        file_size=100,
        mtime=1,
    )
    same_id, changed = store.upsert_track(refreshed)

    assert same_id == track_id
    assert changed is False
    assert store.get_track(track_id).genre == "House"
    assert store.get_track(track_id).year == 2001
    assert np.allclose(store.load_embedding(track_id, "discogs_multi"), vector)


def test_analysis_tasks_claim_retry_and_complete(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    track_path = tmp_path / "track.flac"
    track_path.write_bytes(b"fake")
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=track_path.resolve(),
            artist="Artist",
            title="Title",
            album="Album",
            duration=1.0,
            file_size=4,
            mtime=1,
        )
    )

    job = store.create_analysis_job("discogs_multi", None, max_attempts=2)
    assert job.total == 1

    assert store.claim_analysis_tasks("worker-a", ["other"], limit=10) == []
    tasks = store.claim_analysis_tasks("worker-a", ["discogs_multi"], limit=10, lease_seconds=60)
    assert len(tasks) == 1
    assert tasks[0].track_id == track_id
    assert tasks[0].attempts == 1
    assert store.claim_analysis_tasks("worker-b", ["discogs_multi"], limit=10) == []

    store.fail_analysis_task(
        tasks[0].id,
        error="temporary",
        error_type="RuntimeError",
        stage="predict",
        worker_id="worker-a",
        retryable=True,
    )
    retried = store.claim_analysis_tasks("worker-b", ["discogs_multi"], limit=10)
    assert len(retried) == 1
    assert retried[0].attempts == 2

    store.fail_analysis_task(
        retried[0].id,
        error="broken",
        error_type="RuntimeError",
        stage="predict",
        worker_id="worker-b",
        retryable=True,
    )
    finished = store.get_analysis_job(job.id)
    assert finished is not None
    assert finished.status == "completed"
    assert finished.failed == 1
    assert finished.final_failed == 1


def test_expired_analysis_lease_returns_to_queue(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    track_path = tmp_path / "track.flac"
    track_path.write_bytes(b"fake")
    store.upsert_track(
        ScannedTrack(
            path=track_path.resolve(),
            artist="Artist",
            title="Title",
            album="Album",
            duration=1.0,
            file_size=4,
            mtime=1,
        )
    )
    store.create_analysis_job("discogs_multi", None)
    tasks = store.claim_analysis_tasks("worker-a", ["discogs_multi"], limit=1, lease_seconds=30)
    assert len(tasks) == 1

    assert store.expire_analysis_leases("2999-01-01T00:00:00+00:00") == 1
    reclaimed = store.claim_analysis_tasks("worker-b", ["discogs_multi"], limit=1)
    assert len(reclaimed) == 1
    assert reclaimed[0].lease_owner == "worker-b"


def test_analysis_worker_status_counters_and_release(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    track_path = tmp_path / "track.flac"
    track_path.write_bytes(b"fake")
    store.upsert_track(
        ScannedTrack(
            path=track_path.resolve(),
            artist="Artist",
            title="Title",
            album="Album",
            duration=1.0,
            file_size=4,
            mtime=1,
        )
    )
    store.create_analysis_job("discogs_multi", None)
    store.register_analysis_worker("gpu-1", ["discogs_multi"])

    tasks = store.claim_analysis_tasks("gpu-1", ["discogs_multi"], limit=1)
    assert len(tasks) == 1
    workers = store.list_analysis_workers()
    assert workers[0].worker_id == "gpu-1"
    assert workers[0].claimed_count == 1
    assert workers[0].stage == "claimed"

    released = store.release_analysis_tasks("gpu-1", [tasks[0].id])

    assert released == 1
    worker = store.list_analysis_workers()[0]
    assert worker.released_count == 1
    assert worker.stage == "released"
    assert store.claim_analysis_tasks("gpu-2", ["discogs_multi"], limit=1)[0].lease_owner == "gpu-2"


def test_store_tracks_file_availability_and_delete_missing(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    present = tmp_path / "present.flac"
    present.write_bytes(b"fake")
    missing = tmp_path / "missing.flac"
    present_id, _changed = store.upsert_track(
        ScannedTrack(
            path=present.resolve(),
            artist="Artist",
            title="Present",
            album="Album",
            duration=1.0,
            file_size=4,
            mtime=1,
        )
    )
    missing_id, _changed = store.upsert_track(
        ScannedTrack(
            path=missing.resolve(),
            artist="Artist",
            title="Missing",
            album="Album",
            duration=1.0,
            file_size=4,
            mtime=1,
        )
    )

    checked, missing_count = store.check_file_availability()

    assert checked == 2
    assert missing_count == 1
    assert store.get_track(present_id).missing_at is None
    assert store.get_track(missing_id).missing_at is not None
    assert [track.id for track in store.list_missing_tracks()] == [missing_id]
    assert store.count_missing_files() == 1
    assert store.count_missing_embeddings("discogs_multi") == 1

    assert store.delete_tracks([missing_id]) == 1
    assert store.get_track(missing_id) is None


def test_store_missing_tracks_pagination_and_delete_all(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    ids = []
    for index in range(3):
        track_id, _changed = store.upsert_track(
            ScannedTrack(
                path=(tmp_path / f"missing-{index}.flac").resolve(),
                artist="Artist",
                title=f"Missing {index}",
                album="Album",
                duration=1.0,
                file_size=4,
                mtime=1,
            )
        )
        ids.append(track_id)
        store.mark_track_missing(track_id, f"2026-05-29T20:00:0{index}+00:00")

    first_page = store.list_missing_tracks(limit=2, offset=0)
    second_page = store.list_missing_tracks(limit=2, offset=2)

    assert len(first_page) == 2
    assert len(second_page) == 1
    assert store.delete_missing_tracks() == 3
    assert store.count_missing_files() == 0


def test_store_prediction_round_trip_and_missing_counts(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    first_id, _changed = store.upsert_track(
        ScannedTrack(
            path=(tmp_path / "first.flac").resolve(),
            artist="Artist",
            title="First",
            album="Album",
            genre=None,
            year=None,
            duration=123.0,
            file_size=100,
            mtime=1,
        )
    )
    store.upsert_track(
        ScannedTrack(
            path=(tmp_path / "second.flac").resolve(),
            artist="Artist",
            title="Second",
            album="Album",
            genre=None,
            year=None,
            duration=123.0,
            file_size=100,
            mtime=1,
        )
    )

    store.save_predictions(
        first_id,
        "genre_discogs400",
        [
            TrackPrediction(label="Electronic---Techno", score=0.8, rank=1),
            TrackPrediction(label="Electronic---House", score=0.5, rank=2),
        ],
    )

    predictions = store.load_predictions(first_id, "genre_discogs400")
    assert predictions == [
        TrackPrediction(label="Electronic---Techno", score=0.8, rank=1),
        TrackPrediction(label="Electronic---House", score=0.5, rank=2),
    ]
    assert store.count_predictions("genre_discogs400") == 1
    assert store.count_tracks_missing_predictions("genre_discogs400") == 1
    assert [track.id for track in store.list_tracks_missing_predictions("genre_discogs400")] != [
        first_id
    ]


def test_store_model_output_round_trip_and_missing_pack_counts(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    first_id, _changed = store.upsert_track(
        ScannedTrack(
            path=(tmp_path / "first.flac").resolve(),
            artist="Artist",
            title="First",
            album="Album",
            genre=None,
            year=None,
            duration=123.0,
            file_size=100,
            mtime=1,
        )
    )
    second_id, _changed = store.upsert_track(
        ScannedTrack(
            path=(tmp_path / "second.flac").resolve(),
            artist="Artist",
            title="Second",
            album="Album",
            genre=None,
            year=None,
            duration=123.0,
            file_size=100,
            mtime=1,
        )
    )

    scores = np.array([0.1, 0.2, 0.7], dtype=np.float32)
    store.save_model_output(first_id, "genre_discogs400", scores, "mean_patches")
    store.save_model_output(first_id, "danceability", np.array([0.3, 0.7]), "mean_patches")
    store.save_model_output(second_id, "genre_discogs400", scores, "mean_patches")

    output = store.load_model_output(first_id, "genre_discogs400")
    assert output.model_name == "genre_discogs400"
    assert output.aggregation == "mean_patches"
    assert output.dtype == "float32"
    assert np.allclose(output.scores, scores)
    assert store.count_model_outputs() == 3
    assert store.count_model_outputs("genre_discogs400") == 2
    assert store.count_tracks_missing_head_pack(["genre_discogs400", "danceability"]) == 1
    assert [track.id for track in store.list_tracks_missing_head_pack(["genre_discogs400", "danceability"])] == [
        second_id
    ]


def test_store_feature_round_trip_and_missing_counts(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    first_id, _changed = store.upsert_track(
        ScannedTrack(
            path=(tmp_path / "first.flac").resolve(),
            artist="Artist",
            title="First",
            album="Album",
            genre=None,
            year=None,
            duration=123.0,
            file_size=100,
            mtime=1,
        )
    )
    second_id, _changed = store.upsert_track(
        ScannedTrack(
            path=(tmp_path / "second.flac").resolve(),
            artist="Artist",
            title="Second",
            album="Album",
            genre=None,
            year=None,
            duration=123.0,
            file_size=100,
            mtime=1,
        )
    )

    store.save_features(
        first_id,
        [
            TrackFeature(
                name="bpm",
                value=128.0,
                unit="bpm",
                confidence=0.9,
                extractor="audio_features_v1",
            ),
            TrackFeature(
                name="key",
                text_value="F#",
                confidence=0.7,
                extractor="audio_features_v1",
            ),
        ],
    )

    features = store.load_features(first_id, "audio_features_v1")
    assert [feature.name for feature in features] == ["bpm", "key"]
    assert features[0].value == 128.0
    assert features[1].text_value == "F#"
    assert store.count_feature_tracks("audio_features_v1") == 1
    assert store.count_tracks_missing_features("audio_features_v1") == 1
    assert [track.id for track in store.list_tracks_missing_features("audio_features_v1")] == [
        second_id
    ]


def test_changed_file_scan_removes_predictions(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    scanned = ScannedTrack(
        path=(tmp_path / "track.flac").resolve(),
        artist="Artist",
        title="Title",
        album="Album",
        genre=None,
        year=None,
        duration=123.0,
        file_size=100,
        mtime=1,
    )
    track_id, _changed = store.upsert_track(scanned)
    store.save_predictions(
        track_id,
        "genre_discogs400",
        [TrackPrediction(label="Electronic---Techno", score=0.8, rank=1)],
    )
    store.save_model_output(
        track_id,
        "genre_discogs400",
        np.array([0.8, 0.2], dtype=np.float32),
        "mean_patches",
    )
    store.save_features(
        track_id,
        [TrackFeature(name="bpm", value=128.0, extractor="audio_features_v1")],
    )

    store.upsert_track(
        ScannedTrack(
            path=scanned.path,
            artist="Artist",
            title="Title",
            album="Album",
            genre=None,
            year=None,
            duration=123.0,
            file_size=101,
            mtime=2,
        )
    )

    assert store.load_predictions(track_id, "genre_discogs400") == []
    assert store.load_model_output(track_id, "genre_discogs400") is None
    assert store.load_features(track_id, "audio_features_v1") == []
