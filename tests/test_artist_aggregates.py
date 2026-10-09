from pathlib import Path

import numpy as np

from app.models import ReleaseAggregate, utc_now
from app.scanner import ScannedTrack
from app.services.artist_aggregates import compute_artist_aggregate
from app.services.artist_similarity import catalog_similarity, find_similar_artists
from app.store import Store


def _insert_artist_release_fixture(store: Store) -> None:
    now = utc_now()
    with store.connect() as conn:
        conn.executemany(
            """
            INSERT INTO artists (id, name, normalized_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (1, "Source", "source", now, now),
                (2, "Candidate", "candidate", now, now),
                (3, "Various Artists", "various artists", now, now),
            ],
        )
        conn.executemany(
            """
            INSERT INTO releases (
                id, title, normalized_title, release_type, identity_key,
                identity_confidence, added_at, created_at, updated_at
            ) VALUES (?, ?, ?, 'album', ?, 'exact', ?, ?, ?)
            """,
            [
                (1, "Source One", "source one", "release:1", now, now, now),
                (2, "Source Two", "source two", "release:2", now, now, now),
                (3, "Candidate One", "candidate one", "release:3", now, now, now),
                (4, "Compilation", "compilation", "release:4", now, now, now),
            ],
        )
        conn.executemany(
            """
            INSERT INTO release_artists (
                release_id, artist_id, role, position, confidence, created_at
            ) VALUES (?, ?, 'primary', 0, 'explicit', ?)
            """,
            [(1, 1, now), (2, 1, now), (3, 2, now), (4, 3, now)],
        )

    for release_id in range(1, 5):
        store.upsert_release_aggregate(ReleaseAggregate(
            release_id=release_id,
            track_count=1,
            available_track_count=1,
            duration=None,
            centroid_model="discogs_multi",
            medoid_track_id=None,
            embedding_status="ready",
            top_region_matches_json=None,
            audio_summary_json=None,
            preference_summary_json=None,
            updated_at=now,
        ))
    store.save_release_embedding(1, "discogs_multi", np.array([1.0, 0.0], dtype=np.float32))
    store.save_release_embedding(2, "discogs_multi", np.array([0.0, 1.0], dtype=np.float32))
    store.save_release_embedding(3, "discogs_multi", np.array([0.8, 0.6], dtype=np.float32))
    store.save_release_embedding(4, "discogs_multi", np.array([1.0, 0.0], dtype=np.float32))


def test_artist_aggregate_equal_weights_releases_and_excludes_various_artists(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    _insert_artist_release_fixture(store)

    assert store.list_artist_ids_for_aggregation(model_name="discogs_multi") == [1, 2]

    aggregate = compute_artist_aggregate(store, 1, "discogs_multi")
    vector = store.load_artist_embedding(1, "discogs_multi")

    expected = np.array([1.0, 1.0], dtype=np.float32) / np.sqrt(2.0)
    assert np.allclose(vector, expected)
    assert aggregate.release_count == 2
    assert aggregate.available_release_count == 2
    assert aggregate.medoid_release_id == 1
    assert aggregate.embedding_status == "ready"

    ids, matrix = store.load_all_artist_embeddings("discogs_multi")
    assert ids.tolist() == [1]
    assert matrix.dtype == np.float32
    assert np.allclose(matrix[0], expected)


def test_artist_aggregate_becomes_stale_when_source_release_changes(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    _insert_artist_release_fixture(store)
    compute_artist_aggregate(store, 1, "discogs_multi")

    assert store.list_artist_ids_for_aggregation(model_name="discogs_multi") == [2]
    with store.connect() as conn:
        conn.execute(
            "UPDATE release_aggregates SET updated_at = ? WHERE release_id = 1",
            ("9999-01-01T00:00:00+00:00",),
        )
    assert store.list_artist_ids_for_aggregation(model_name="discogs_multi") == [1, 2]

    with store.connect() as conn:
        conn.execute(
            "UPDATE release_aggregates SET embedding_status = 'unavailable' WHERE release_id IN (1, 2)"
        )
    unavailable = compute_artist_aggregate(store, 1, "discogs_multi")
    assert unavailable.embedding_status == "unavailable"
    assert store.load_artist_embedding(1, "discogs_multi") is None


def test_artist_similarity_uses_symmetric_catalog_coverage(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    _insert_artist_release_fixture(store)
    compute_artist_aggregate(store, 1, "discogs_multi")
    compute_artist_aggregate(store, 2, "discogs_multi")

    first_catalog = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    second_catalog = np.array([[1.0, 0.0]], dtype=np.float32)
    assert catalog_similarity(first_catalog, second_catalog) == 0.75

    results = find_similar_artists(store, "discogs_multi", 1, limit=16)
    assert [result.artist_id for result in results] == [2]
    assert results[0].catalog_similarity > 0.0
    assert results[0].score == (
        0.6 * results[0].centroid_similarity + 0.4 * results[0].catalog_similarity
    )


def _add_remix(store: Store, *, remixer: str, vector: list[float], name: str = "tune") -> int:
    track_id, _changed = store.upsert_track(ScannedTrack(
        path=Path(f"/music/Candidate/Remixes/{name}.flac"),
        artist="Candidate",
        title=f"{name} ({remixer} Remix)",
        album="Remixes",
        duration=200.0,
        file_size=1,
        mtime=1,
    ))
    store.save_embedding(track_id, "discogs_multi", np.array(vector, dtype=np.float32))
    return track_id


def test_remix_only_artist_gets_a_profile_from_remixes_on_foreign_releases(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    _insert_artist_release_fixture(store)
    _add_remix(store, remixer="Remixer", vector=[0.0, 1.0])
    remixer_id = store.artist_ids_by_names(["Remixer"])["remixer"]

    assert remixer_id in store.list_artist_ids_for_aggregation(model_name="discogs_multi")
    aggregate = compute_artist_aggregate(store, remixer_id, "discogs_multi")

    assert aggregate.embedding_status == "ready"
    assert aggregate.release_count == 0
    assert aggregate.available_release_count == 1
    assert np.allclose(store.load_artist_embedding(remixer_id, "discogs_multi"), [0.0, 1.0])
    # Пересчитан — больше не устаревший; каталог для похожих включает ремикс.
    assert remixer_id not in store.list_artist_ids_for_aggregation(model_name="discogs_multi")
    assert store.load_release_embeddings_for_artists([remixer_id], "discogs_multi")[remixer_id].shape == (1, 2)


def test_artist_profile_blends_own_releases_with_remix_units(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    _insert_artist_release_fixture(store)
    # «Source» (свои релизы [1,0] и [0,1]) сделал два ремикса на одном чужом релизе.
    _add_remix(store, remixer="Source", vector=[1.0, 0.0], name="one")
    _add_remix(store, remixer="Source", vector=[0.0, 1.0], name="two")

    aggregate = compute_artist_aggregate(store, 1, "discogs_multi")

    # Чужой релиз — один юнит (среднее ремиксов), наравне со своими двумя.
    assert aggregate.available_release_count == 3
    assert aggregate.release_count == 2
    expected = np.array([1.0, 1.0], dtype=np.float32) / np.sqrt(2.0)
    assert np.allclose(store.load_artist_embedding(1, "discogs_multi"), expected, atol=1e-6)
    # Медоид — свой релиз, не чужой с ремиксом.
    assert aggregate.medoid_release_id in {1, 2}
    assert store.load_release_embeddings_for_artists([1], "discogs_multi")[1].shape == (3, 2)
