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
