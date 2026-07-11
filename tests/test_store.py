from pathlib import Path

import numpy as np
import pytest

from app.scanner import ScannedTrack
from app.store import PlaybackEventCreate, Store, TrackFeature, TrackPrediction


def playback_event(event_type: str, **kwargs) -> PlaybackEventCreate:
    return PlaybackEventCreate(event_type=event_type, **kwargs)


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


def test_added_at_is_preserved_and_release_added_at_is_derived(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    track_path = (tmp_path / "added" / "01.flac").resolve()
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=track_path,
            artist="Added Artist",
            title="First",
            album="Added Release",
            duration=100.0,
            file_size=1,
            mtime=1,
        )
    )
    first = store.get_track(track_id)
    assert first is not None
    assert first.added_at is not None

    same_id, changed = store.upsert_track(
        ScannedTrack(
            path=track_path,
            artist="Added Artist",
            title="First Updated",
            album="Added Release",
            duration=101.0,
            file_size=2,
            mtime=2,
        )
    )
    updated = store.get_track(same_id)
    assert changed is True
    assert updated is not None
    assert updated.added_at == first.added_at

    second_id, _changed = store.upsert_track(
        ScannedTrack(
            path=(tmp_path / "added" / "02.flac").resolve(),
            artist="Added Artist",
            title="Second",
            album="Added Release",
            duration=100.0,
            file_size=1,
            mtime=1,
        )
    )
    release = store.search_entities("Added Release")["releases"]["items"][0].release
    with store.connect() as conn:
        row = conn.execute(
            """
            SELECT r.added_at AS release_added_at, MAX(t.added_at) AS max_track_added_at
            FROM releases r
            JOIN release_tracks rt ON rt.release_id = r.id
            JOIN tracks t ON t.id = rt.track_id
            WHERE r.id = ?
            """,
            (release.id,),
        ).fetchone()

    assert second_id != track_id
    assert row["release_added_at"] == row["max_track_added_at"]


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


def test_external_track_mapping_round_trip_and_idempotent_upsert(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=Path("navidrome://song-1"),
            artist="Artist",
            title="Title",
            album="Album",
            genre="Techno",
            year=2001,
            duration=123.0,
            file_size=100,
            mtime=1,
        )
    )

    mapping = store.upsert_external_track(
        "navidrome",
        "song-1",
        track_id,
        raw_json='{"id":"song-1"}',
        synced_at="2026-06-02T10:00:00+00:00",
    )
    refreshed = store.upsert_external_track(
        "navidrome",
        "song-1",
        track_id,
        raw_json='{"id":"song-1","title":"Title"}',
        synced_at="2026-06-02T11:00:00+00:00",
    )

    assert mapping.provider == "navidrome"
    assert refreshed.track_id == track_id
    assert refreshed.raw_json == '{"id":"song-1","title":"Title"}'
    assert refreshed.synced_at == "2026-06-02T11:00:00+00:00"
    assert store.count_external_tracks("navidrome") == 1
    assert store.get_external_track("navidrome", "song-1") == refreshed
    assert store.get_track_by_external_id("navidrome", "song-1").id == track_id
    assert store.external_id_for_track("navidrome", track_id) == "song-1"
    assert store.list_external_tracks("navidrome") == [refreshed]


def test_external_track_replaces_old_provider_mapping_for_track(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=Path("navidrome://old-song"),
            artist="Artist",
            title="Title",
            album="Album",
            duration=123.0,
            file_size=100,
            mtime=1,
        )
    )
    store.upsert_external_track("navidrome", "old-song", track_id)

    mapping = store.upsert_external_track("navidrome", "new-song", track_id)

    assert mapping.external_id == "new-song"
    assert store.get_external_track("navidrome", "old-song") is None
    assert store.external_id_for_track("navidrome", track_id) == "new-song"
    assert store.count_external_tracks("navidrome") == 1
    assert store.count_external_ids("navidrome", "track") == 1
    with store.connect() as conn:
        stale = conn.execute(
            """
            SELECT 1 FROM external_ids
            WHERE provider = 'navidrome'
              AND entity_type = 'track'
              AND external_id = 'old-song'
            """
        ).fetchone()
    assert stale is None


def test_external_track_cascades_when_track_is_deleted(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=Path("navidrome://song-1"),
            artist="Artist",
            title="Title",
            album="Album",
            duration=123.0,
            file_size=100,
            mtime=1,
        )
    )
    store.upsert_external_track("navidrome", "song-1", track_id)

    assert store.delete_tracks([track_id]) == 1

    assert store.get_track_by_external_id("navidrome", "song-1") is None
    assert store.get_external_track("navidrome", "song-1") is None
    assert store.count_external_tracks("navidrome") == 0


def test_external_track_rejects_missing_track_and_empty_keys(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()

    try:
        store.upsert_external_track("navidrome", "song-1", 999)
    except ValueError as exc:
        assert str(exc) == "Track not found: 999"
    else:
        raise AssertionError("missing track should fail")

    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=Path("navidrome://song-1"),
            artist="Artist",
            title="Title",
            album="Album",
            duration=123.0,
            file_size=100,
            mtime=1,
        )
    )
    for provider, external_id, expected in [
        ("", "song-1", "provider must not be empty"),
        ("navidrome", "", "external_id must not be empty"),
    ]:
        try:
            store.upsert_external_track(provider, external_id, track_id)
        except ValueError as exc:
            assert str(exc) == expected
        else:
            raise AssertionError("empty external mapping value should fail")


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


def test_create_analysis_job_without_tracks_finishes_immediately(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()

    job = store.create_analysis_job("discogs_multi", None)

    assert job.status == "completed"
    assert job.total == 0
    assert job.done == 0
    assert job.failed == 0
    assert job.message == "Analyzed 0 tracks, failed 0"
    assert job.finished_at is not None


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
    refreshed = store.get_analysis_job(tasks[0].job_id)
    assert refreshed is not None
    assert refreshed.status == "running"
    assert refreshed.queued == 1
    assert refreshed.leased == 0
    assert store.claim_analysis_tasks("gpu-2", ["discogs_multi"], limit=1)[0].lease_owner == "gpu-2"


def test_analysis_worker_heartbeat_skips_fresh_write(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    store.register_analysis_worker("gpu-1", ["discogs_multi"])
    initial = store.list_analysis_workers()[0]

    wrote = store.heartbeat_analysis_worker("gpu-1", ["discogs_multi"], min_interval_seconds=60)

    refreshed = store.list_analysis_workers()[0]
    assert wrote is False
    assert refreshed.last_seen_at == initial.last_seen_at


def test_cancel_analysis_job_stops_running_queue(tmp_path: Path):
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
            mtime=1,
            file_size=4,
        )
    )
    job = store.create_analysis_job("discogs_multi", None)
    task = store.claim_analysis_tasks("gpu-1", ["discogs_multi"], limit=1)[0]

    cancelled = store.cancel_analysis_job(job.id, "test cancel")

    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.failed == 1
    assert store.claim_analysis_tasks("gpu-2", ["discogs_multi"], limit=1) == []
    assert store.get_analysis_task(task.id).status == "final_failed"


def test_analysis_job_task_summary_reports_leases_and_errors(tmp_path: Path):
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
            mtime=1,
            file_size=4,
        )
    )
    job = store.create_analysis_job("discogs_multi", None)
    task = store.claim_analysis_tasks("gpu-1", ["discogs_multi"], limit=1)[0]
    summary = store.analysis_job_task_summary(job.id)
    assert summary["leased_workers"] == [{"worker_id": "gpu-1", "count": 1}]
    assert summary["oldest_lease"]["worker_id"] == "gpu-1"
    assert summary["status_breakdown"][0]["status"] == "leased"

    store.fail_analysis_task(
        task.id,
        error="missing dependency",
        error_type="RuntimeError",
        stage="worker",
        worker_id="gpu-1",
        retryable=False,
    )
    summary = store.analysis_job_task_summary(job.id)
    assert summary["recent_errors"][0]["error"] == "missing dependency"


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


def test_file_availability_treats_navidrome_tracks_as_available(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=Path("navidrome://song-1"),
            artist="Artist",
            title="Remote",
            album="Album",
            duration=123.0,
            file_size=100,
            mtime=1,
        )
    )
    store.upsert_external_track("navidrome", "song-1", track_id)
    store.mark_track_missing(track_id)

    checked, missing_count = store.check_file_availability()

    assert checked == 1
    assert missing_count == 0
    assert store.get_track(track_id).missing_at is None
    assert store.count_missing_files() == 0


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


def test_top_prediction_by_track_returns_rank1_and_skips_missing(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    ids = []
    for name in ("first", "second", "third"):
        track_id, _changed = store.upsert_track(
            ScannedTrack(
                path=(tmp_path / f"{name}.flac").resolve(),
                artist="Artist",
                title=name,
                album="Album",
                genre=None,
                year=None,
                duration=123.0,
                file_size=100 + len(name),
                mtime=1,
            )
        )
        ids.append(track_id)

    store.save_predictions(
        ids[0],
        "genre_discogs400",
        [
            TrackPrediction(label="Electronic---Techno", score=0.8, rank=1),
            TrackPrediction(label="Electronic---House", score=0.5, rank=2),
        ],
    )
    store.save_predictions(
        ids[1],
        "genre_discogs400",
        [TrackPrediction(label="Rock---Punk", score=0.6, rank=1)],
    )
    # A different head for ids[0] must not leak into the genre lookup.
    store.save_predictions(
        ids[0],
        "mtg_jamendo_genre",
        [TrackPrediction(label="ambient", score=0.9, rank=1)],
    )

    tops = store.top_prediction_by_track("genre_discogs400", ids)

    # Only rank-1 rows, only the requested head; ids[2] (no prediction) absent.
    assert tops == {
        ids[0]: ("Electronic---Techno", 0.8),
        ids[1]: ("Rock---Punk", 0.6),
    }
    assert store.top_prediction_by_track("genre_discogs400", []) == {}


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
    assert store.count_model_outputs_by_model(["genre_discogs400", "danceability", "unused_model"]) == {
        "genre_discogs400": 2,
        "danceability": 1,
        "unused_model": 0,
    }


def test_store_features_round_trip_and_missing_features_counts(tmp_path: Path):
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

    assert [track.id for track in store.list_active_tracks()] == [first_id, second_id]
    deleted = store.delete_features_for_tracks([first_id], "audio_features_v1")

    assert deleted == 2
    assert store.load_features(first_id, "audio_features_v1") == []
    assert store.count_tracks_missing_features("audio_features_v1") == 2


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


def test_upsert_track_creates_normalized_artist_release_sidecars(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()

    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=(tmp_path / "Artist" / "Album" / "01 - Title.flac").resolve(),
            artist="Alpha & Beta",
            title="Title",
            album="Album",
            album_artist="Alpha",
            duration=120.0,
            file_size=100,
            mtime=1,
            track_number=1,
            disc_number=1,
        )
    )

    status = store.normalization_status()
    assert status.total_tracks == 1
    assert status.tracks_with_release == 1
    assert status.tracks_with_artist == 1
    assert status.releases == 1
    assert status.artists == 2

    search = store.search_entities("Alpha")
    release = search["releases"]["items"][0]
    assert release.release.title == "Album"
    assert [artist.name for artist in release.artists] == ["Alpha"]
    tracks = store.list_release_tracks(release.release.id)
    assert [item.track.id for item in tracks] == [track_id]
    assert tracks[0].track_number == 1
    assert [artist.name for artist in tracks[0].artists] == ["Alpha", "Beta"]


def test_release_artists_fall_back_to_various_artists_without_dominant_artist(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    release_dir = tmp_path / "Various" / "Split"

    first_id, _changed = store.upsert_track(
        ScannedTrack(
            path=(release_dir / "01 - One.flac").resolve(),
            artist="Alpha",
            title="One",
            album="Split",
            duration=120.0,
            file_size=100,
            mtime=1,
            track_number=1,
        )
    )
    second_id, _changed = store.upsert_track(
        ScannedTrack(
            path=(release_dir / "02 - Two.flac").resolve(),
            artist="Beta",
            title="Two",
            album="Split",
            duration=130.0,
            file_size=101,
            mtime=1,
            track_number=2,
        )
    )

    releases = store.search_entities("Split")["releases"]["items"]
    assert len(releases) == 1
    release = releases[0]
    # No dominant artist — Alpha and Beta each cover 50% of the release, below the
    # 60% threshold — so the release credit falls back to the synthetic "Various Artists".
    assert [artist.name for artist in release.artists] == ["Various Artists"]
    tracks = store.list_release_tracks(release.release.id)
    assert [item.track.id for item in tracks] == [first_id, second_id]
    # Track-level artists stay intact even though the release aggregates to VA.
    assert [artist.name for artist in tracks[0].artists] == ["Alpha"]
    assert [artist.name for artist in tracks[1].artists] == ["Beta"]


def test_track_release_move_refreshes_old_release_sidecars(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    track_path = (tmp_path / "Artist" / "Old Album" / "01 - Title.flac").resolve()

    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=track_path,
            artist="Alpha",
            title="Title",
            album="Old Album",
            album_artist="Old Artist",
            duration=120.0,
            file_size=100,
            mtime=1,
            track_number=1,
        )
    )
    old_release_id = store.search_entities("Old Album")["releases"]["items"][0].release.id

    refreshed_id, changed = store.upsert_track(
        ScannedTrack(
            path=track_path,
            artist="Alpha",
            title="Title",
            album="New Album",
            album_artist="New Artist",
            duration=120.0,
            file_size=101,
            mtime=2,
            track_number=1,
        )
    )

    assert refreshed_id == track_id
    assert changed is True
    old_release = store.get_release(old_release_id)
    assert old_release is not None
    assert old_release.release.track_count == 0
    assert old_release.artists == []
    assert store.list_release_tracks(old_release_id) == []
    assert store.search_entities("Old Album")["releases"]["total"] == 0
    new_release = store.search_entities("New Album")["releases"]["items"][0]
    assert new_release.release.track_count == 1
    assert [artist.name for artist in new_release.artists] == ["New Artist"]


def test_normalization_backfill_is_idempotent_and_mirrors_external_ids(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=Path("navidrome://song-1"),
            artist="Artist",
            title="Title",
            album="Album",
            duration=123.0,
            file_size=100,
            mtime=1,
        )
    )
    store.upsert_external_track(
        "navidrome",
        "song-1",
        track_id,
        raw_json='{"albumId":"album-1","albumArtist":"Album Artist","coverArt":"cover-1"}',
    )

    first = store.backfill_library_normalization()
    second = store.backfill_library_normalization()

    assert first.tracks_with_release == 1
    assert second.tracks_with_release == 1
    assert first.releases == second.releases
    assert second.artists == 2
    assert second.orphan_releases == 1
    assert store.count_external_ids("navidrome", "track") == 1
    assert store.count_external_ids("navidrome", "release") == 1
    releases = store.search_entities("Album")["releases"]["items"]
    release = next(
        item.release
        for item in releases
        if item.release.identity_key.startswith("provider:navidrome")
    )
    assert release.identity_key == "provider:navidrome:release:album-1"
    assert release.cover_art_id == "cover-1"


def test_normalization_backfill_mirrors_navidrome_artist_id(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=Path("navidrome://song-1"),
            artist="Artist",
            title="Title",
            album="Album",
            duration=123.0,
            file_size=100,
            mtime=1,
        )
    )
    store.upsert_external_track(
        "navidrome",
        "song-1",
        track_id,
        raw_json='{"albumId":"album-1","artistId":"artist-1"}',
    )

    store.backfill_library_normalization()
    artist_id = store.search_entities("Artist")["artists"]["items"][0].artist.id

    assert store.external_id_for_entity("navidrome", "artist", artist_id) == "artist-1"

    store.update_artist_external_info(artist_id, image_url="https://lastfm.example/artist.jpg", bio="Bio")
    artist = store.get_artist(artist_id)
    assert artist is not None
    assert artist.artist.image_url == "https://lastfm.example/artist.jpg"
    assert artist.artist.bio == "Bio"


def test_missing_album_creates_synthetic_one_track_release(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()

    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=(tmp_path / "loose.flac").resolve(),
            artist=None,
            title="Loose Track",
            album=None,
            duration=60.0,
            file_size=100,
            mtime=1,
        )
    )

    status = store.normalization_status()
    assert status.releases == 1
    release = store.search_entities("Loose Track")["releases"]["items"][0]
    assert release.release.title == "Loose Track"
    assert release.release.release_type == "unknown"
    assert [item.track.id for item in store.list_release_tracks(release.release.id)] == [track_id]


def test_playback_session_queue_round_trip(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    first_id, _changed = store.upsert_track(
        ScannedTrack(
            path=(tmp_path / "album" / "01.flac").resolve(),
            artist="Alpha",
            title="First",
            album="Playback",
            duration=180.0,
            file_size=1,
            mtime=1,
        )
    )
    second_id, _changed = store.upsert_track(
        ScannedTrack(
            path=(tmp_path / "album" / "02.flac").resolve(),
            artist="Alpha",
            title="Second",
            album="Playback",
            duration=180.0,
            file_size=1,
            mtime=1,
        )
    )

    session, queue = store.create_playback_session(
        source_type="release",
        source_id=1,
        source_label="Playback",
        track_ids=[first_id, second_id],
        autoplay_enabled=True,
        settings={"visible_queue_size": 5},
    )

    assert session.source_type == "release"
    assert session.autoplay_enabled is True
    assert len(queue) == 2
    assert [item.track_id for item in queue] == [first_id, second_id]
    assert store.get_playback_session(session.id).current_track_id == first_id

    updated = store.update_playback_session(
        session.id,
        status="paused",
        shuffle_enabled=True,
        repeat_mode="all",
        state={"position_seconds": 12.0},
    )
    assert updated.status == "paused"
    assert updated.shuffle_enabled is True
    assert updated.repeat_mode == "all"

    moved = store.move_queue_item(session.id, queue[1].id, 0)
    assert [item.track_id for item in moved] == [second_id, first_id]


def test_playback_queue_click_is_navigation_not_skip(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=(tmp_path / "track.flac").resolve(),
            artist="Navigator",
            title="Jump",
            album="Playback",
            duration=200.0,
            file_size=1,
            mtime=1,
        )
    )
    session, queue = store.create_playback_session(source_type="track", source_id=track_id, track_ids=[track_id])

    result = store.record_playback_event(
        playback_event(
            "queue_click",
            session_id=session.id,
            queue_item_id=queue[0].id,
            position_seconds=5.0,
            duration_seconds=200.0,
            client_event_id="queue-click-1",
        )
    )

    assert result.duplicate is False
    assert store.get_playback_session(session.id).current_queue_item_id == queue[0].id
    assert store.get_track_preference(track_id) is None
    assert store.list_playback_events(session.id)[0].event_type == "queue_click"


def test_playback_skip_strength_and_recompute_from_raw_events(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    early_id, _changed = store.upsert_track(
        ScannedTrack(
            path=(tmp_path / "early.flac").resolve(),
            artist="Skip Artist",
            title="Early",
            album="Skips",
            duration=240.0,
            file_size=1,
            mtime=1,
        )
    )
    late_id, _changed = store.upsert_track(
        ScannedTrack(
            path=(tmp_path / "late.flac").resolve(),
            artist="Skip Artist",
            title="Late",
            album="Skips",
            duration=240.0,
            file_size=1,
            mtime=1,
        )
    )

    store.record_playback_event(
        playback_event(
            "skipped",
            track_id=early_id,
            position_seconds=10.0,
            duration_seconds=240.0,
            client_event_id="early-skip",
        )
    )
    store.record_playback_event(
        playback_event(
            "skipped",
            track_id=late_id,
            position_seconds=220.0,
            duration_seconds=240.0,
            client_event_id="late-skip",
        )
    )

    early = store.get_track_preference(early_id)
    late = store.get_track_preference(late_id)
    release_id = store.search_entities("Skips")["releases"]["items"][0].release.id
    artist_id = store.search_entities("Skip Artist")["artists"]["items"][0].artist.id
    release_pref = store.get_release_preference(release_id)
    artist_pref = store.get_artist_preference(artist_id)
    assert early.skip_count == 1
    assert early.early_skip_count == 1
    assert late.skip_count == 1
    assert late.early_skip_count == 0
    assert early.score < late.score
    assert release_pref.skip_count == 2
    assert artist_pref.skip_count == 2
    assert release_pref.score == artist_pref.score
    assert release_pref.score > early.score

    before = (early.skip_count, early.early_skip_count, early.score, late.skip_count, late.score)
    store.recompute_user_preferences()
    early_after = store.get_track_preference(early_id)
    late_after = store.get_track_preference(late_id)
    assert (
        early_after.skip_count,
        early_after.early_skip_count,
        early_after.score,
        late_after.skip_count,
        late_after.score,
    ) == before


def test_playback_completion_like_dislike_replay_save_and_duplicate_idempotency(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=(tmp_path / "positive.flac").resolve(),
            artist="Positive Artist",
            title="Positive",
            album="Signals",
            duration=120.0,
            file_size=1,
            mtime=1,
        )
    )
    release_id = store.search_entities("Signals")["releases"]["items"][0].release.id
    artist_id = store.search_entities("Positive Artist")["artists"]["items"][0].artist.id

    store.record_playback_event(playback_event("play_threshold_reached", track_id=track_id, client_event_id="threshold"))
    store.record_playback_event(playback_event("completed", track_id=track_id, play_fraction=0.5, client_event_id="low-complete"))
    store.record_playback_event(playback_event("completed", track_id=track_id, play_fraction=0.95, client_event_id="complete"))
    store.record_playback_event(playback_event("liked", track_id=track_id, client_event_id="liked"))
    duplicate = store.record_playback_event(playback_event("liked", track_id=track_id, client_event_id="liked"))
    store.record_playback_event(playback_event("disliked", track_id=track_id, client_event_id="disliked"))
    store.record_playback_event(playback_event("replayed", track_id=track_id, client_event_id="replayed"))
    store.record_playback_event(playback_event("saved_to_playlist", track_id=track_id, client_event_id="saved"))
    store.record_playback_event(playback_event("removed_from_queue", track_id=track_id, client_event_id="removed"))

    pref = store.get_track_preference(track_id)
    assert duplicate.duplicate is True
    assert pref.play_count == 1
    assert pref.completion_count == 1
    assert pref.liked is False
    assert pref.disliked is True
    assert pref.replay_count == 1

    release_pref = store.get_release_preference(release_id)
    artist_pref = store.get_artist_preference(artist_id)
    assert release_pref.play_count == 1
    assert release_pref.completion_count == 1
    assert artist_pref.play_count == 1
    assert artist_pref.completion_count == 1

    store.recompute_user_preferences()
    recomputed = store.get_track_preference(track_id)
    assert recomputed.play_count == pref.play_count
    assert recomputed.completion_count == pref.completion_count
    assert recomputed.disliked == pref.disliked
    assert recomputed.replay_count == pref.replay_count


def test_top_tracks_for_artist_orders_by_play_count_desc(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()

    def make_track(name: str) -> int:
        track_id, _changed = store.upsert_track(
            ScannedTrack(
                path=(tmp_path / f"{name}.flac").resolve(),
                artist="Shared Artist",
                title=name,
                album="Album",
                duration=120.0,
                file_size=1,
                mtime=1,
            )
        )
        return track_id

    quiet_id = make_track("Quiet Track")
    loud_id = make_track("Loud Track")
    medium_id = make_track("Medium Track")
    artist_id = store.search_entities("Shared Artist")["artists"]["items"][0].artist.id

    for _ in range(3):
        store.record_playback_event(playback_event("play_threshold_reached", track_id=loud_id))
    store.record_playback_event(playback_event("play_threshold_reached", track_id=medium_id))

    top = store.top_tracks_for_artist(artist_id, limit=5)

    assert [track.id for track, _count in top] == [loud_id, medium_id, quiet_id]
    assert [count for _track, count in top] == [3, 1, 0]


def test_top_tracks_for_artist_excludes_other_artists(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    own_id, _changed = store.upsert_track(
        ScannedTrack(
            path=(tmp_path / "own.flac").resolve(),
            artist="Own Artist",
            title="Own Track",
            album="Album",
            duration=120.0,
            file_size=1,
            mtime=1,
        )
    )
    store.upsert_track(
        ScannedTrack(
            path=(tmp_path / "other.flac").resolve(),
            artist="Other Artist",
            title="Other Track",
            album="Album",
            duration=120.0,
            file_size=1,
            mtime=1,
        )
    )
    store.record_playback_event(playback_event("play_threshold_reached", track_id=own_id))
    artist_id = store.search_entities("Own Artist")["artists"]["items"][0].artist.id

    top = store.top_tracks_for_artist(artist_id, limit=5)

    assert [track.id for track, _count in top] == [own_id]


def test_playback_queue_rejects_missing_tracks_and_low_completion_does_not_finish_item(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.init()
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=(tmp_path / "queue.flac").resolve(),
            artist="Queue Artist",
            title="Queue Track",
            album="Queue Release",
            duration=100.0,
            file_size=1,
            mtime=1,
        )
    )

    with pytest.raises(ValueError, match="Tracks not found: 999"):
        store.create_playback_session(source_type="track", source_id=999, track_ids=[999])

    session, queue = store.create_playback_session(source_type="track", source_id=track_id, track_ids=[track_id])
    with pytest.raises(ValueError, match="Tracks not found: 999"):
        store.append_queue_items(session.id, [{"track_id": 999}])
    with pytest.raises(ValueError, match="Tracks not found: 999"):
        store.replace_queue_items(session.id, [{"track_id": 999}])

    store.record_playback_event(
        playback_event(
            "completed",
            session_id=session.id,
            queue_item_id=queue[0].id,
            track_id=track_id,
            play_fraction=0.5,
        )
    )

    assert store.list_queue_items(session.id)[0].status == "queued"
    assert store.get_track_preference(track_id).completion_count == 0
