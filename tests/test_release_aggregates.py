import numpy as np

from app.models import ReleaseAggregate, utc_now
from app.store import Store


def test_release_embedding_round_trip_normalizes_and_orders_rows(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()

    empty_ids, empty_matrix = store.load_all_release_embeddings("discogs_multi")
    assert empty_ids.shape == (0,)
    assert empty_matrix.shape == (0, 0)

    now = utc_now()
    with store.connect() as conn:
        conn.executemany(
            """
            INSERT INTO releases (
                id, title, normalized_title, release_type, identity_key,
                identity_confidence, added_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "Release 1", "release 1", "album", "release:1", "exact", now, now, now),
                (2, "Release 2", "release 2", "album", "release:2", "exact", now, now, now),
            ],
        )

    store.upsert_release_aggregate(
        ReleaseAggregate(
            release_id=2,
            track_count=3,
            available_track_count=3,
            duration=1800.0,
            centroid_model="discogs_multi",
            medoid_track_id=None,
            embedding_status="ready",
            top_region_matches_json="{}",
            audio_summary_json="{}",
            preference_summary_json="{}",
            updated_at=now,
        )
    )
    store.upsert_release_aggregate(
        ReleaseAggregate(
            release_id=1,
            track_count=2,
            available_track_count=2,
            duration=1200.0,
            centroid_model="discogs_multi",
            medoid_track_id=None,
            embedding_status="ready",
            top_region_matches_json="{}",
            audio_summary_json="{}",
            preference_summary_json="{}",
            updated_at=now,
        )
    )
    store.save_release_embedding(2, "discogs_multi", np.array([0.0, 3.0], dtype=np.float32))
    store.save_release_embedding(1, "discogs_multi", np.array([4.0, 0.0], dtype=np.float32))

    first = store.load_release_embedding(1, "discogs_multi")
    second = store.load_release_embedding(2, "discogs_multi")
    ids, matrix = store.load_all_release_embeddings("discogs_multi")

    assert np.allclose(first, np.array([1.0, 0.0], dtype=np.float32))
    assert np.allclose(second, np.array([0.0, 1.0], dtype=np.float32))
    assert ids.tolist() == [1, 2]
    assert matrix.dtype == np.float32
    assert matrix.shape == (2, 2)
    assert np.allclose(matrix[0], first)
    assert np.allclose(matrix[1], second)
