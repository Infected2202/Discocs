from pathlib import Path
import base64
import json
import sqlite3
import socket
from threading import Event
from urllib.error import URLError

import numpy as np
from fastapi.testclient import TestClient

import app.main as main_module
import app.store as store_module
from app.cli import normalize_worker_models, worker_failure_retryable
from app.audio_features import AUDIO_FEATURE_EXTRACTOR
from app.config import Settings
from app.head_pack import HeadOutput, Prediction
from app.main import app
from app.navidrome import DownloadedTrack
from app.navidrome import NavidromeSong
from app.scanner import ScannedTrack
from app.store import INITIALIZED_DB_PATHS, Store, Track, TrackFeature, TrackPrediction


def init_api_store(tmp_path: Path, monkeypatch) -> Store:
    clear_runtime_jobs()
    db_path = tmp_path / "app.db"
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    monkeypatch.setenv("DISCOCS_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.delenv("DISCOCS_NAVIDROME_URL", raising=False)
    monkeypatch.delenv("DISCOCS_NAVIDROME_USER", raising=False)
    monkeypatch.delenv("DISCOCS_NAVIDROME_PASSWORD", raising=False)
    monkeypatch.delenv("DISCOCS_NAVIDROME_AUTH_MODE", raising=False)
    store = Store(db_path)
    store.init()
    return store


def clear_runtime_jobs() -> None:
    with main_module.JOBS_LOCK:
        main_module.JOBS.clear()
    with main_module.DEFERRED_JOBS_LOCK:
        main_module.DEFERRED_JOB_ORDER.clear()
        main_module.DEFERRED_JOB_STARTERS.clear()


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


def add_navidrome_track(
    store: Store,
    item_id: str,
    title: str = "Title",
    artist: str = "Artist",
    album: str = "Album",
) -> int:
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=f"navidrome://{item_id}",  # type: ignore[arg-type]
            artist=artist,
            title=title,
            album=album,
            duration=123.0,
            file_size=100,
            mtime=0,
        )
    )
    return track_id


def test_health():
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_v1_search_artist_release_and_track_groups(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    add_track(store, tmp_path / "album" / "one.flac", title="One", artist="Solee", album="Grind")
    client = TestClient(app)

    response = client.get("/api/v1/search?q=Solee")

    assert response.status_code == 200
    data = response.json()
    assert data["query"] == "Solee"
    assert data["top_result"]["entity_type"] == "artist"
    groups = {group["type"]: group for group in data["groups"]}
    assert groups["artists"]["items"][0]["name"] == "Solee"
    assert groups["tracks"]["items"][0]["title"] == "One"
    assert groups["releases"]["items"][0]["title"] == "Grind"


def test_api_v1_search_track_summaries_include_normalized_artists(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    add_track(
        store,
        tmp_path / "album" / "duo.flac",
        title="Duo Track",
        artist="Alpha & Beta",
        album="Collab",
    )
    client = TestClient(app)

    response = client.get("/api/v1/search?q=Duo%20Track")

    assert response.status_code == 200
    groups = {group["type"]: group for group in response.json()["groups"]}
    track = groups["tracks"]["items"][0]
    assert track["title"] == "Duo Track"
    assert [artist["name"] for artist in track["artists"]] == ["Alpha", "Beta"]


def test_api_v1_release_and_tracks_contract(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    add_track(store, tmp_path / "album" / "01.flac", title="First", artist="Alpha", album="Release")
    release_id = store.search_entities("Release")["releases"]["items"][0].release.id
    client = TestClient(app)

    release_response = client.get(f"/api/v1/releases/{release_id}")
    tracks_response = client.get(f"/api/v1/releases/{release_id}/tracks")
    recommendations_response = client.get(f"/api/v1/releases/{release_id}/recommendations")

    assert release_response.status_code == 200
    release = release_response.json()["release"]
    assert release["title"] == "Release"
    assert release["release_type"] == "unknown"
    assert release["artists"] == [{"id": release["artists"][0]["id"], "name": "Alpha"}]
    assert tracks_response.status_code == 200
    assert tracks_response.json()["items"][0]["title"] == "First"
    assert recommendations_response.json()["available"] is False


def test_api_v1_artist_discography_groups_unknown_releases(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    add_track(store, tmp_path / "album" / "one.flac", title="One", artist="Alpha", album="Unknown Type")
    artist_id = store.search_entities("Alpha")["artists"]["items"][0].artist.id
    client = TestClient(app)

    artist_response = client.get(f"/api/v1/artists/{artist_id}")
    discography_response = client.get(f"/api/v1/artists/{artist_id}/discography")
    top_tracks_response = client.get(f"/api/v1/artists/{artist_id}/top-tracks")
    similar_response = client.get(f"/api/v1/artists/{artist_id}/similar")

    assert artist_response.status_code == 200
    assert artist_response.json()["artist"]["name"] == "Alpha"
    groups = {group["key"]: group for group in discography_response.json()["groups"]}
    assert groups["releases"]["items"][0]["title"] == "Unknown Type"
    assert top_tracks_response.json()["available"] is False
    assert similar_response.json()["basis"] == "not_available"


def test_api_v1_missing_entity_uses_error_envelope(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.get("/api/v1/releases/999")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "Release not found"}
    }


def test_api_v1_playback_create_get_and_patch_release_session(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    add_track(store, tmp_path / "album" / "01.flac", title="One", artist="Alpha", album="Playback")
    add_track(store, tmp_path / "album" / "02.flac", title="Two", artist="Alpha", album="Playback")
    release_id = store.search_entities("Playback")["releases"]["items"][0].release.id
    client = TestClient(app)

    created = client.post(
        "/api/v1/playback/sessions",
        json={
            "source_type": "release",
            "source_id": release_id,
            "source_label": "Playback",
            "autoplay_enabled": True,
            "settings": {"visible_queue_size": 5},
        },
    )

    assert created.status_code == 200
    body = created.json()
    session_id = body["session"]["id"]
    assert body["session"]["source_type"] == "release"
    assert body["session"]["autoplay_enabled"] is True
    assert [item["track"]["title"] for item in body["queue"]["items"]] == ["One", "Two"]

    restored = client.get(f"/api/v1/playback/sessions/{session_id}")
    assert restored.status_code == 200
    assert restored.json()["queue"]["current_index"] == 0

    patched = client.patch(
        f"/api/v1/playback/sessions/{session_id}",
        json={"status": "paused", "shuffle_enabled": True, "repeat_mode": "all"},
    )
    assert patched.status_code == 200
    assert patched.json()["session"]["status"] == "paused"
    assert patched.json()["session"]["shuffle_enabled"] is True
    assert patched.json()["session"]["repeat_mode"] == "all"


def test_api_v1_playback_queue_patch_jump_records_navigation_not_skip(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    first_id = add_track(store, tmp_path / "queue" / "01.flac", title="First")
    second_id = add_track(store, tmp_path / "queue" / "02.flac", title="Second")
    third_id = add_track(store, tmp_path / "queue" / "03.flac", title="Third")
    client = TestClient(app)
    session_id = client.post(
        "/api/v1/playback/sessions",
        json={"source_type": "track", "source_id": first_id},
    ).json()["session"]["id"]

    added = client.patch(
        f"/api/v1/playback/sessions/{session_id}/queue",
        json={"operation": "add", "track_ids": [second_id, third_id]},
    )
    assert added.status_code == 200
    assert [item["track_id"] for item in added.json()["queue"]["items"]] == [first_id, second_id, third_id]
    third_item_id = added.json()["queue"]["items"][2]["id"]

    moved = client.patch(
        f"/api/v1/playback/sessions/{session_id}/queue",
        json={"operation": "move", "queue_item_id": third_item_id, "position": 1},
    )
    assert moved.status_code == 200
    assert [item["track_id"] for item in moved.json()["queue"]["items"]] == [first_id, third_id, second_id]

    jumped = client.patch(
        f"/api/v1/playback/sessions/{session_id}/queue",
        json={"operation": "jump", "queue_item_id": third_item_id},
    )
    assert jumped.status_code == 200
    assert jumped.json()["session"]["current_track_id"] == third_id
    assert store.get_track_preference(third_id) is None
    events = store.list_playback_events(session_id)
    assert [event.event_type for event in events] == ["queue_click"]


def test_api_v1_playback_event_ingest_updates_preferences_and_is_idempotent(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    track_id = add_track(store, tmp_path / "signals.flac", title="Signals", artist="Alpha")
    client = TestClient(app)

    threshold = client.post(
        "/api/v1/playback/events",
        json={
            "track_id": track_id,
            "event_type": "play_threshold_reached",
            "position_seconds": 31.0,
            "duration_seconds": 120.0,
            "client_event_id": "threshold-1",
        },
    )
    duplicate = client.post(
        "/api/v1/playback/events",
        json={"track_id": track_id, "event_type": "play_threshold_reached", "client_event_id": "threshold-1"},
    )
    skipped = client.post(
        "/api/v1/playback/events",
        json={
            "track_id": track_id,
            "event_type": "skipped",
            "position_seconds": 8.0,
            "duration_seconds": 120.0,
            "client_event_id": "skip-1",
        },
    )

    assert threshold.status_code == 200
    assert threshold.json()["accepted"] is True
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert skipped.status_code == 200
    pref = store.get_track_preference(track_id)
    assert pref.play_count == 1
    assert pref.skip_count == 1
    assert pref.early_skip_count == 1


def test_api_v1_playback_event_rejects_invalid_event_type(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.post(
        "/api/v1/playback/events",
        json={"event_type": "not_a_real_event", "track_id": 1},
    )

    assert response.status_code == 422


def test_api_v1_dashboard_shelves_use_library_and_playback_history(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    old_id = add_track(store, tmp_path / "old" / "01.flac", title="Old One", artist="Alpha", album="Old Release")
    new_id = add_track(store, tmp_path / "new" / "01.flac", title="New One", artist="Beta", album="New Release")
    with store.connect() as conn:
        conn.execute("UPDATE tracks SET created_at = ? WHERE id = ?", ("2024-01-01T00:00:00+00:00", old_id))
        conn.execute("UPDATE tracks SET created_at = ? WHERE id = ?", ("2026-01-01T00:00:00+00:00", new_id))
    store.record_playback_event(
        track_id=old_id,
        event_type="play_threshold_reached",
        created_at="2024-02-01T00:00:00+00:00",
    )
    store.record_playback_event(
        track_id=old_id,
        event_type="completed",
        created_at="2024-02-01T00:03:00+00:00",
    )
    client = TestClient(app)

    response = client.get("/api/v1/dashboard?limit=5")

    assert response.status_code == 200
    data = response.json()
    assert data["hero"]["title"] == "Flow"
    shelves = {shelf["key"]: shelf for shelf in data["shelves"]}
    assert shelves["recently_added"]["items"][0]["title"] == "New Release"
    assert shelves["listen_again"]["items"][0]["title"] == "Old One"
    assert shelves["long_time_no_listen"]["items"][0]["title"] == "Old One"

    shelf_response = client.get("/api/v1/dashboard/shelves/listen_again?limit=1")
    assert shelf_response.status_code == 200
    assert shelf_response.json()["next_offset"] is None

    missing_response = client.get("/api/v1/dashboard/shelves/not_real")
    assert missing_response.status_code == 404


def test_listener_surface_routes_serve_shell():
    client = TestClient(app)

    for path in ["/search", "/artists/1", "/releases/1"]:
        response = client.get(path)
        assert response.status_code == 200
        assert "listenerSearch" in response.text
        assert "artistSurface" in response.text
        assert "releaseSurface" in response.text


def test_navidrome_sync_job_imports_catalog(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)

    class FakeNavidromeClient:
        def __init__(self, _settings):
            pass

        def iter_songs(self, *, page_size: int, query: str = "", limit: int | None = None):
            assert page_size == 10
            assert limit is None
            yield NavidromeSong(
                id="song-1",
                title="One",
                artist="Artist",
                album="Album",
                duration=123,
                size=100,
                suffix="flac",
                raw={"id": "song-1", "title": "One"},
            )

    monkeypatch.setattr(main_module, "NavidromeClient", FakeNavidromeClient)
    client = TestClient(app)

    response = client.post(
        "/jobs/navidrome-sync",
        json={"page_size": 10, "mark_stale": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["page_size"] == 10
    imported = store.get_track_by_external_id("navidrome", "song-1")
    assert imported is not None
    assert imported.path == "navidrome://song-1"
    jobs = client.get("/jobs?include_completed=true").json()["jobs"]
    sync_job = next(job for job in jobs if job["id"] == body["job_id"])
    assert sync_job["status"] == "completed"
    assert "seen=1 imported=1" in sync_job["message"]


def test_test_ui_loads():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "discocs" in response.text
    assert "Dashboard" in response.text
    assert "listenerDashboardShelves" in response.text
    assert "/api/v1/dashboard?limit=8" in response.text
    assert "Search library" in response.text
    assert "listenerSearch" in response.text
    assert "/api/v1/search" in response.text
    assert "artistSurface" in response.text
    assert "releaseSurface" in response.text
    assert "playSource('release'" in response.text
    assert "Library" in response.text
    assert "Browse" in response.text
    assert "Metrics" in response.text
    assert "metricsSummary" in response.text
    assert "Discogs-EffNet heads" in response.text
    assert "/metrics/search" in response.text
    assert "coverMarkup(t)" in response.text
    assert "Lost files" in response.text
    assert "Errored files" in response.text
    assert "Recommendations" in response.text
    assert "Navidrome likes" in response.text
    assert "navidromeLikes" in response.text
    assert "Load Navidrome likes" in response.text
    assert "Recommendations from liked blend" in response.text
    assert "addToBlendExtra" in response.text
    assert "discocs.blendExtra.v1" in response.text
    assert "/navidrome/starred" in response.text
    assert "Evaluation" in response.text
    assert "Jobs" in response.text
    assert "Settings" in response.text
    assert "Sync Navidrome" in response.text
    assert "Analyze missing" in response.text
    assert "Download head models" in response.text
    assert "Analyze Discogs-EffNet heads" in response.text
    assert "Analyze audio features" in response.text
    assert "Audio feature workers" in response.text
    assert 'id="audioFeatureWorkers"' in response.text
    assert "Navidrome" in response.text
    assert "Save Navidrome" in response.text
    assert "Sync catalog" in response.text
    assert "startScan" not in response.text
    assert "/jobs/scan" not in response.text
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
    assert "function syncModelRoute()" in response.text
    assert 'addEventListener("change", onModelChange)' in response.text
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
    assert "section-fill" in response.text
    assert "panel-fill" in response.text
    assert "list-region" in response.text
    assert "max-height: 58vh" not in response.text
    assert "Add seed" in response.text
    assert "instantMixCountCollaborationArtists" in response.text
    assert "openAnalysis" in response.text
    assert "analysisModal" in response.text
    assert "icon-tablet" in response.text
    assert "Start session" in response.text
    assert "browse/facets" in response.text
    assert "facet-list" in response.text
    assert "compactFolder" in response.text
    assert "function routeTo(" in response.text
    assert "function applyRoute(" in response.text
    assert "function initFromUrl(" in response.text
    assert 'window.addEventListener("popstate"' in response.text
    assert "function activateSection(" in response.text
    assert "function restoreRecommendations(" in response.text
    assert 'view: "recommendations"' in response.text
    assert "seed: String(id)" in response.text


def test_navidrome_settings_round_trip(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    initial = client.get("/settings/navidrome")

    assert initial.status_code == 200
    assert initial.json()["password_set"] is False

    saved = client.put(
        "/settings/navidrome",
        json={
            "url": "http://navidrome:4533",
            "user": "tester",
            "password": "secret",
            "auth_mode": "token",
            "timeout_seconds": 45,
            "download_mode": "download",
            "temp_dir": str(tmp_path / "navidrome-tmp"),
        },
    )

    assert saved.status_code == 200
    body = saved.json()
    assert body["url"] == "http://navidrome:4533"
    assert body["user"] == "tester"
    assert body["password_set"] is True
    assert "password" not in body
    settings = Settings.from_env()
    assert settings.navidrome.url == "http://navidrome:4533"
    assert settings.navidrome.user == "tester"
    assert settings.navidrome.password == "secret"


def test_navidrome_plugin_event_is_accepted(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.post(
        "/navidrome/plugin-event",
        json={
            "event": "similar_called",
            "item_id": "song-1",
            "model": "discogs_multi",
            "count": 50,
            "discocs_url": "http://discocs:8711",
            "message": "debug",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_worker_media_failures_are_not_retryable():
    terminal_errors = [
        RuntimeError("ffmpeg decoded no audio samples from /tmp/bad.mp3"),
        RuntimeError("ffmpeg failed to decode /tmp/bad.mp3 with exit code 1: Invalid data found when processing input"),
        RuntimeError("Output file #0 does not contain any stream"),
        ValueError("Embedding vector has zero norm"),
    ]

    for error in terminal_errors:
        assert worker_failure_retryable(error) is False


def test_normalize_worker_models_splits_compact_model_lists():
    assert normalize_worker_models(
        [
            "discogs_multi,discogs_track",
            "discogs_release discogs_label",
            "audio_features_v1",
            "discogs_multi",
        ]
    ) == [
        "discogs_multi",
        "discogs_track",
        "discogs_release",
        "discogs_label",
        "audio_features_v1",
    ]


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
    assert "index_status" in data
    assert "index_count" in data
    assert "index_embedding_count" in data


def test_audio_feature_missing_count_ignores_missing_files(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    ready_id = add_track(store, tmp_path / "ready.flac", title="Ready")
    missing_feature_id = add_track(store, tmp_path / "missing-feature.flac", title="Missing Feature")
    missing_file_id = add_track(store, tmp_path / "missing-file.flac", title="Missing File")
    store.save_features(
        ready_id,
        [TrackFeature("bpm", value=128.0, unit="bpm", extractor=AUDIO_FEATURE_EXTRACTOR)],
    )
    store.save_features(
        missing_file_id,
        [TrackFeature("bpm", value=130.0, unit="bpm", extractor=AUDIO_FEATURE_EXTRACTOR)],
    )
    store.mark_track_missing(missing_file_id, "2026-05-29T20:00:00+00:00")
    with main_module.STATS_CACHE_LOCK:
        main_module.STATS_CACHE.clear()
    client = TestClient(app)

    data = client.get("/stats").json()

    assert data["audio_features_missing_tracks"] == 1
    assert missing_feature_id != missing_file_id


def test_stats_reports_stale_index_counts(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    first_path = tmp_path / "first.flac"
    second_path = tmp_path / "second.flac"
    for path in [first_path, second_path]:
        path.write_bytes(b"fake")
    first_id = add_track(store, first_path, title="First")
    second_id = add_track(store, second_path, title="Second")
    store.save_embedding(first_id, "discogs_multi", np.array([1.0, 0.0], dtype=np.float32))
    main_module.build_index(store, main_module.Settings.from_env(), "discogs_multi")
    client = TestClient(app)

    with main_module.STATS_CACHE_LOCK:
        main_module.STATS_CACHE.clear()
    ready = client.get("/stats?model=discogs_multi").json()
    assert ready["index_status"] == "ready"
    assert ready["index_count"] == 1
    assert ready["index_embedding_count"] == 1

    store.save_embedding(second_id, "discogs_multi", np.array([0.0, 1.0], dtype=np.float32))
    with main_module.STATS_CACHE_LOCK:
        main_module.STATS_CACHE.clear()
    stale = client.get("/stats?model=discogs_multi").json()
    assert stale["index_status"] == "stale"
    assert stale["index_count"] == 1
    assert stale["index_embedding_count"] == 2


def test_metrics_features_summary_and_search(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    first_id = add_track(store, tmp_path / "first.flac", title="First", artist="A")
    second_id = add_track(store, tmp_path / "second.flac", title="Second", artist="B")
    store.save_features(
        first_id,
        [
            TrackFeature("bpm", value=128.0, unit="bpm", extractor=AUDIO_FEATURE_EXTRACTOR),
            TrackFeature("key", text_value="A", extractor=AUDIO_FEATURE_EXTRACTOR),
            TrackFeature("scale", text_value="minor", extractor=AUDIO_FEATURE_EXTRACTOR),
        ],
    )
    store.save_features(
        second_id,
        [
            TrackFeature("bpm", value=142.0, unit="bpm", extractor=AUDIO_FEATURE_EXTRACTOR),
            TrackFeature("key", text_value="C", extractor=AUDIO_FEATURE_EXTRACTOR),
            TrackFeature("scale", text_value="major", extractor=AUDIO_FEATURE_EXTRACTOR),
        ],
    )
    client = TestClient(app)

    summary = client.get("/metrics/features").json()
    bpm = next(item for item in summary["features"] if item["name"] == "bpm")
    assert bpm["track_count"] == 2
    assert bpm["min_value"] == 128.0
    assert bpm["max_value"] == 142.0
    values = client.get("/metrics/features/key/values").json()["values"]
    assert values == [{"value": "A", "track_count": 1}, {"value": "C", "track_count": 1}]

    response = client.post(
        "/metrics/search",
        json={
            "filters": [
                {"name": "bpm", "min_value": 120, "max_value": 130},
                {"name": "scale", "text_values": ["minor"]},
            ],
            "sort_by": "bpm",
            "limit": 20,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["results"][0]["id"] == first_id
    assert {feature["name"] for feature in data["results"][0]["features"]} == {
        "bpm",
        "key",
        "scale",
    }


def test_metrics_heads_summary_and_search(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    first_id = add_track(store, tmp_path / "first.flac", title="First", artist="A")
    second_id = add_track(store, tmp_path / "second.flac", title="Second", artist="B")
    store.save_model_output(
        first_id,
        "genre_discogs400",
        np.array([0.85, 0.15], dtype=np.float32),
        "mean_patches",
    )
    store.save_model_output(
        second_id,
        "genre_discogs400",
        np.array([0.25, 0.75], dtype=np.float32),
        "mean_patches",
    )
    store.save_predictions(
        first_id,
        "genre_discogs400",
        [
            TrackPrediction(label="Electronic---Techno", score=0.85, rank=1),
            TrackPrediction(label="Electronic---House", score=0.15, rank=2),
        ],
    )
    store.save_predictions(
        second_id,
        "genre_discogs400",
        [
            TrackPrediction(label="Electronic---House", score=0.75, rank=1),
            TrackPrediction(label="Electronic---Techno", score=0.25, rank=2),
        ],
    )
    client = TestClient(app)

    summary = client.get("/metrics/features?source=heads").json()
    genre = next(item for item in summary["features"] if item["name"] == "genre_discogs400")
    assert genre["track_count"] == 2
    assert genre["text_count"] == 0
    assert genre["max_value"] == 1.0
    labels = client.get(
        "/metrics/features/genre_discogs400/values?source=heads"
    ).json()["values"]
    assert labels[0]["value"] in {"Electronic---House", "Electronic---Techno"}

    response = client.post(
        "/metrics/search",
        json={
            "source": "heads",
            "filters": [
                {
                    "name": "genre_discogs400",
                    "min_value": 0.8,
                    "text_values": ["Electronic---Techno"],
                },
            ],
            "sort_by": "genre_discogs400",
            "sort_direction": "desc",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["results"][0]["id"] == first_id
    assert data["results"][0]["features"][0]["name"] == "genre_discogs400"
    assert data["results"][0]["features"][0]["text_value"] == "Electronic---Techno"


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


def test_worker_heartbeat_skips_fresh_write(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)
    register = client.post(
        "/workers/register",
        json={"worker_id": "gpu-1", "models": ["discogs_multi"]},
    )
    initial = store.list_analysis_workers()[0]

    heartbeat = client.post(
        "/workers/heartbeat",
        json={"worker_id": "gpu-1", "models": ["discogs_multi"]},
    )

    refreshed = store.list_analysis_workers()[0]
    assert register.status_code == 200
    assert heartbeat.status_code == 200
    assert heartbeat.json()["heartbeat_written"] is False
    assert refreshed.last_seen_at == initial.last_seen_at


def test_worker_register_reports_sqlite_disk_io_error(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)

    def fail_register(self, worker_id, models):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(store_module.Store, "register_analysis_worker", fail_register)
    client = TestClient(app)

    response = client.post(
        "/workers/register",
        json={"worker_id": "gpu-1", "models": ["discogs_multi"]},
    )

    assert response.status_code == 503
    assert "SQLite disk I/O error" in response.json()["detail"]


def test_worker_audio_endpoint_downloads_navidrome_track(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path="navidrome://song-1",  # type: ignore[arg-type]
            artist="Artist",
            title="Title",
            album="Album",
            duration=123.0,
            file_size=100,
            mtime=0,
        )
    )
    store.upsert_external_track("navidrome", "song-1", track_id)
    job = store.create_analysis_job("discogs_multi", None, local_executor_enabled=False)

    class FakeNavidromeClient:
        def __init__(self, _settings):
            pass

        def download_track(self, item_id: str, target_dir: Path, *, suffix: str | None = None):
            assert item_id == "song-1"
            target_dir.mkdir(parents=True, exist_ok=True)
            path = target_dir / "song-1.audio"
            path.write_bytes(b"navidrome-audio")
            return DownloadedTrack(
                path=path,
                bytes_written=len(b"navidrome-audio"),
                content_type="audio/flac",
                mode="download",
            )

    monkeypatch.setattr("app.audio_source.NavidromeClient", FakeNavidromeClient)
    client = TestClient(app)
    task = client.post(
        "/workers/claim",
        json={"worker_id": "gpu-1", "models": ["discogs_multi"], "limit": 4},
    ).json()["tasks"][0]

    response = client.get(task["audio_url"])

    assert response.status_code == 200
    assert response.content == b"navidrome-audio"
    assert store.get_analysis_job(job.id).leased == 1
    assert not (tmp_path / "tmp" / "navidrome" / "song-1.audio").exists()


def test_local_embedding_uses_navidrome_downloaded_audio(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path="navidrome://song-1",  # type: ignore[arg-type]
            artist="Artist",
            title="Title",
            album="Album",
            duration=123.0,
            file_size=100,
            mtime=0,
        )
    )
    store.upsert_external_track("navidrome", "song-1", track_id)
    track = store.get_track(track_id)
    seen_paths: list[Path] = []

    class FakeNavidromeClient:
        def __init__(self, _settings):
            pass

        def download_track(self, item_id: str, target_dir: Path, *, suffix: str | None = None):
            target_dir.mkdir(parents=True, exist_ok=True)
            path = target_dir / "song-1.audio"
            path.write_bytes(b"navidrome-audio")
            return DownloadedTrack(
                path=path,
                bytes_written=len(b"navidrome-audio"),
                content_type="audio/flac",
                mode="download",
            )

    class FakeEmbedder:
        def extract_track_vector(self, path: Path):
            seen_paths.append(path)
            assert path.read_bytes() == b"navidrome-audio"
            return np.array([0.1, 0.2, 0.3], dtype=np.float32)

    monkeypatch.setattr("app.audio_source.NavidromeClient", FakeNavidromeClient)

    result = main_module._extract_embedding_local(
        FakeEmbedder(),
        store,
        main_module.Settings.from_env(),
        track,
    )

    assert result.status == "ok"
    assert np.allclose(result.vector, np.array([0.1, 0.2, 0.3], dtype=np.float32))
    assert seen_paths == [tmp_path / "tmp" / "navidrome" / "song-1.audio"]
    assert not seen_paths[0].exists()


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

    jobs = client.get("/jobs?detail=true")
    assert jobs.status_code == 200
    assert "workers" not in jobs.json()
    first_job = jobs.json()["jobs"][0]
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
    jobs = client.get("/jobs?include_completed=true").json()["jobs"]
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


def test_job_created_while_memory_job_runs_is_deferred(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    clear_runtime_jobs()
    active_id = main_module.create_job("check-missing-files", "active")
    main_module.update_job(active_id, status="running", message="active")
    ran = Event()
    started: list[tuple[str, str]] = []

    def fake_index_job(job_id: str, model: str) -> None:
        started.append((job_id, model))
        main_module.finish_job(job_id, "completed", "fake index done")
        ran.set()

    monkeypatch.setattr(main_module, "_index_job", fake_index_job)
    client = TestClient(app)

    response = client.post("/jobs/index", json={"model": "discogs_multi"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "deferred"
    assert not ran.is_set()
    jobs = client.get("/jobs?include_completed=true").json()["jobs"]
    deferred = next(job for job in jobs if job["id"] == body["job_id"])
    assert jobs[0]["id"] == body["job_id"]
    assert deferred["status"] == "deferred"
    assert deferred["queue_position"] == 1

    main_module.finish_job(active_id, "completed", "active done")

    assert ran.wait(2)
    assert started == [(body["job_id"], "discogs_multi")]


def test_deferred_job_starts_after_remote_analysis_completes(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    clear_runtime_jobs()
    path = tmp_path / "track.flac"
    path.write_bytes(b"fake-audio")
    add_track(store, path)
    ran = Event()
    started: list[tuple[str, str]] = []

    def fake_index_job(job_id: str, model: str) -> None:
        started.append((job_id, model))
        main_module.finish_job(job_id, "completed", "fake index done")
        ran.set()

    monkeypatch.setattr(main_module, "_index_job", fake_index_job)
    client = TestClient(app)
    analysis_response = client.post(
        "/jobs/analyze",
        json={"model": "discogs_multi", "limit": 1, "execution_mode": "remote"},
    )
    index_response = client.post("/jobs/index", json={"model": "discogs_multi"})

    assert analysis_response.status_code == 200
    assert index_response.status_code == 200
    assert index_response.json()["status"] == "deferred"
    assert not ran.is_set()
    with store.connect() as conn:
        conn.execute(
            "UPDATE analysis_tasks SET status = 'completed' WHERE job_id = ?",
            (analysis_response.json()["job_id"],),
        )
    main_module.run_maintenance_tick(store)

    jobs = client.get("/jobs?include_completed=true").json()["jobs"]

    assert ran.wait(2)
    assert started == [(index_response.json()["job_id"], "discogs_multi")]
    assert any(job["id"] == analysis_response.json()["job_id"] and job["status"] == "completed" for job in jobs)


def test_finished_transient_job_elapsed_is_frozen(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    clear_runtime_jobs()
    times = iter([100.0, 130.0])
    monkeypatch.setattr(main_module, "perf_counter", lambda: next(times))
    job_id = main_module.create_job("download-head-models", "test")
    main_module.finish_job(job_id, "completed", "done")
    monkeypatch.setattr(main_module, "perf_counter", lambda: 999.0)
    client = TestClient(app)

    jobs = client.get("/jobs?include_completed=true").json()["jobs"]

    job = next(item for item in jobs if item["id"] == job_id)
    assert job["status"] == "completed"
    assert job["elapsed_seconds"] == 30.0
    assert job["eta_seconds"] is None


def test_finished_durable_job_elapsed_is_frozen(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    path = tmp_path / "track.flac"
    path.write_bytes(b"fake-audio")
    add_track(store, path)
    monkeypatch.setattr(store_module, "utc_now", lambda: "2026-01-01T00:00:00+00:00")
    job = store.create_analysis_job("discogs_multi", None)
    with store.connect() as conn:
        conn.execute(
            "UPDATE analysis_tasks SET status = 'completed' WHERE job_id = ?",
            (job.id,),
        )
    monkeypatch.setattr(store_module, "utc_now", lambda: "2026-01-01T00:00:30+00:00")
    store.refresh_active_analysis_jobs()
    client = TestClient(app)

    first = next(
        item for item in client.get("/jobs?include_completed=true").json()["jobs"] if item["id"] == job.id
    )
    monkeypatch.setattr(store_module, "utc_now", lambda: "2026-01-01T00:10:00+00:00")
    second = next(
        item for item in client.get("/jobs?include_completed=true").json()["jobs"] if item["id"] == job.id
    )

    assert first["status"] == "completed"
    assert first["elapsed_seconds"] == 30.0
    assert second["elapsed_seconds"] == 30.0
    assert second["finished_at_iso"] == "2026-01-01T00:00:30+00:00"


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
    jobs = client.get("/jobs?detail=true").json()["jobs"]
    assert "Waiting for worker supporting audio_features_v1" in jobs[0]["status_hint"]
    claim = client.post(
        "/workers/claim",
        json={"worker_id": "gpu-1", "models": [AUDIO_FEATURE_EXTRACTOR], "limit": 1},
    )
    assert claim.status_code == 200
    tasks = claim.json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["track_id"] == track_id


def test_analyze_audio_features_reset_existing_requeues_processed_track(
    tmp_path: Path,
    monkeypatch,
):
    store = init_api_store(tmp_path, monkeypatch)
    path = tmp_path / "track.flac"
    path.write_bytes(b"fake-audio")
    track_id = add_track(store, path)
    store.save_features(
        track_id,
        [
            TrackFeature(
                "bpm",
                value=172.265,
                unit="bpm",
                extractor=AUDIO_FEATURE_EXTRACTOR,
            )
        ],
    )
    client = TestClient(app)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("remote-only must not start local audio feature executor")

    monkeypatch.setattr(main_module, "_analyze_audio_features_job", fail_if_called)
    response = client.post(
        "/jobs/analyze-audio-features",
        json={
            "execution_mode": "remote",
            "local_executor_enabled": False,
            "reset_existing": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["reset_existing"] is True
    assert response.json()["deleted_features"] == 1
    assert store.load_features(track_id, AUDIO_FEATURE_EXTRACTOR) == []
    job = store.get_analysis_job(response.json()["job_id"])
    assert job is not None
    assert job.queued == 1
    claim = client.post(
        "/workers/claim",
        json={"worker_id": "gpu-1", "models": [AUDIO_FEATURE_EXTRACTOR], "limit": 1},
    )
    assert claim.status_code == 200
    assert claim.json()["tasks"][0]["track_id"] == track_id


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
    jobs = client.get("/jobs?detail=true").json()["jobs"]
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


def test_similar_mix_returns_blended_results(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    first_path = tmp_path / "seed-a.flac"
    second_path = tmp_path / "seed-b.flac"
    result_path = tmp_path / "result.flac"
    for path in [first_path, second_path, result_path]:
        path.write_bytes(b"fake")
    first_id = add_track(store, first_path, title="Seed A", artist="A")
    second_id = add_track(store, second_path, title="Seed B", artist="B")
    result_id = add_track(store, result_path, title="Result", artist="C", album="Other")
    store.save_embedding(first_id, "discogs_multi", np.array([1.0, 0.0], dtype=np.float32))
    store.save_embedding(second_id, "discogs_multi", np.array([0.0, 1.0], dtype=np.float32))
    store.save_embedding(result_id, "discogs_multi", np.array([0.7, 0.7], dtype=np.float32))
    main_module.build_index(store, main_module.Settings.from_env(), "discogs_multi")
    client = TestClient(app)

    response = client.get(
        f"/tracks/similar/mix?seed_ids={first_id},{second_id}&model=discogs_multi&k=5"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["blend"] == "average"
    assert {seed["id"] for seed in data["seeds"]} == {first_id, second_id}
    assert data["skipped_seed_ids"] == []
    assert [item["id"] for item in data["results"]] == [result_id]


def test_similar_mix_reports_missing_tracks(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.get("/tracks/similar/mix?seed_ids=999")

    assert response.status_code == 404
    assert "Tracks not found" in response.json()["detail"]


def test_similar_mix_rejects_empty_seed_ids(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.get("/tracks/similar/mix?seed_ids=")

    assert response.status_code == 400


def test_similar_mix_rejects_invalid_seed_ids(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.get("/tracks/similar/mix?seed_ids=1,abc")

    assert response.status_code == 400
    assert "Invalid seed id" in response.json()["detail"]


def test_similar_mix_reports_missing_index(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    seed_path = tmp_path / "seed.flac"
    seed_path.write_bytes(b"fake")
    seed_id = add_track(store, seed_path, title="Seed")
    store.save_embedding(seed_id, "discogs_multi", np.array([1.0, 0.0], dtype=np.float32))
    client = TestClient(app)

    response = client.get(f"/tracks/similar/mix?seed_ids={seed_id}&model=discogs_multi")

    assert response.status_code == 503
    assert "Index not found" in response.json()["detail"]


def test_similar_reports_stale_index(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    seed_path = tmp_path / "seed.flac"
    result_path = tmp_path / "result.flac"
    for path in [seed_path, result_path]:
        path.write_bytes(b"fake")
    seed_id = add_track(store, seed_path, title="Seed")
    result_id = add_track(store, result_path, title="Result", album="Two")
    store.save_embedding(seed_id, "discogs_multi", np.array([1.0, 0.0], dtype=np.float32))
    main_module.build_index(store, main_module.Settings.from_env(), "discogs_multi")
    store.save_embedding(result_id, "discogs_multi", np.array([0.9, 0.1], dtype=np.float32))
    client = TestClient(app)

    response = client.get(f"/tracks/{seed_id}/similar?model=discogs_multi&k=1")

    assert response.status_code == 503
    assert "Index is stale" in response.json()["detail"]


def test_similar_mix_reports_missing_embeddings(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    seed_path = tmp_path / "seed.flac"
    seed_path.write_bytes(b"fake")
    seed_id = add_track(store, seed_path, title="Seed")
    client = TestClient(app)

    response = client.get(f"/tracks/similar/mix?seed_ids={seed_id}&model=discogs_multi")

    assert response.status_code == 404
    assert "No embeddings for mix seeds" in response.json()["detail"]


def test_similar_mix_skips_seeds_without_embeddings(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    ready_path = tmp_path / "ready.flac"
    missing_path = tmp_path / "missing.flac"
    result_path = tmp_path / "result.flac"
    for path in [ready_path, missing_path, result_path]:
        path.write_bytes(b"fake")
    ready_id = add_track(store, ready_path, title="Ready")
    missing_id = add_track(store, missing_path, title="Missing")
    result_id = add_track(store, result_path, title="Result", album="Two")
    store.save_embedding(ready_id, "discogs_multi", np.array([1.0, 0.0], dtype=np.float32))
    store.save_embedding(result_id, "discogs_multi", np.array([0.9, 0.1], dtype=np.float32))
    main_module.build_index(store, main_module.Settings.from_env(), "discogs_multi")
    client = TestClient(app)

    response = client.get(
        f"/tracks/similar/mix?seed_ids={ready_id},{missing_id}&model=discogs_multi&k=1"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["skipped_seed_ids"] == [missing_id]
    assert [item["id"] for item in data["results"]] == [result_id]


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
    store.save_features(
        result_id,
        [
            TrackFeature("bpm", value=128.0, unit="bpm", extractor=AUDIO_FEATURE_EXTRACTOR),
            TrackFeature("key", text_value="C#", extractor=AUDIO_FEATURE_EXTRACTOR),
            TrackFeature("scale", text_value="minor", extractor=AUDIO_FEATURE_EXTRACTOR),
        ],
    )
    store.save_predictions(
        result_id,
        "genre_discogs400",
        [
            TrackPrediction(label="Electronic---Techno", score=0.7, rank=1),
            TrackPrediction(label="Electronic---Minimal", score=0.2, rank=2),
            TrackPrediction(label="Electronic---Tech House", score=0.1, rank=3),
        ],
    )
    store.save_predictions(
        result_id,
        "approachability_3c",
        [TrackPrediction(label="approachable", score=0.8231, rank=1)],
    )
    store.save_predictions(
        result_id,
        "engagement_3c",
        [TrackPrediction(label="engaging", score=0.74, rank=1)],
    )
    store.upsert_external_track(
        "navidrome",
        "result-song",
        result_id,
        raw_json=json.dumps({"suffix": "flac", "bitRate": 914}),
    )
    main_module.build_index(store, main_module.Settings.from_env(), "discogs_multi")
    client = TestClient(app)

    response = client.get(f"/tracks/{seed_id}/similar?model=discogs_multi&k=1")

    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["rating"] == 3
    assert result["card_features"]["bpm"]["value"] == 128.0
    assert result["card_features"]["key"]["text_value"] == "C#"
    assert result["card_features"]["scale"]["text_value"] == "minor"
    assert [item["label"] for item in result["genre_discogs400"]] == [
        "Electronic---Techno",
        "Electronic---Minimal",
        "Electronic---Tech House",
    ]
    assert result["approachability_3c"]["score"] == 0.8231
    assert result["engagement_3c"]["score"] == 0.74
    assert result["audio_format"] == "FLAC"
    assert result["bitrate"] == 914


def _configure_navidrome_env(monkeypatch) -> None:
    monkeypatch.setenv("DISCOCS_NAVIDROME_URL", "http://navidrome:4533")
    monkeypatch.setenv("DISCOCS_NAVIDROME_USER", "alice")
    monkeypatch.setenv("DISCOCS_NAVIDROME_PASSWORD", "secret")
    monkeypatch.setenv("DISCOCS_NAVIDROME_AUTH_MODE", "plain")


def test_navidrome_starred_catalog(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    _configure_navidrome_env(monkeypatch)

    class FakeNavidromeClient:
        def __init__(self, settings):
            self.settings = settings

        def get_starred_songs(self):
            return [
                NavidromeSong(id="like-1", title="Like One", artist="A", album="Album"),
                NavidromeSong(id="like-2", title="Like Two", artist="B", album="Other"),
                NavidromeSong(id="ghost", title="Ghost", artist="C"),
            ]

    monkeypatch.setattr(main_module, "NavidromeClient", FakeNavidromeClient)
    ready_id = add_navidrome_track(store, "like-1", title="Like One", artist="A")
    missing_id = add_navidrome_track(store, "like-2", title="Like Two", artist="B")
    store.upsert_external_track("navidrome", "like-1", ready_id)
    store.upsert_external_track("navidrome", "like-2", missing_id)
    store.save_embedding(ready_id, "discogs_multi", np.array([1.0, 0.0], dtype=np.float32))
    client = TestClient(app)

    response = client.get("/navidrome/starred?model=discogs_multi")

    assert response.status_code == 200
    data = response.json()
    assert data["user"] == "alice"
    assert data["count"] == 3
    assert data["mapped_count"] == 2
    assert data["ready_count"] == 1
    assert data["missing_embedding_count"] == 1
    assert data["not_synced_count"] == 1
    statuses = {item["item_id"]: item["status"] for item in data["results"]}
    assert statuses["like-1"] == "ready"
    assert statuses["like-2"] == "missing_embedding"
    assert statuses["ghost"] == "not_synced"


def test_navidrome_starred_ids_maps_current_user_likes(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    _configure_navidrome_env(monkeypatch)

    class FakeNavidromeClient:
        def __init__(self, settings):
            self.settings = settings

        def get_starred_songs(self):
            return [
                NavidromeSong(id="like-1", title="Like One", artist="A"),
                NavidromeSong(id="ghost", title="Ghost", artist="C"),
            ]

    monkeypatch.setattr(main_module, "NavidromeClient", FakeNavidromeClient)
    track_id = add_navidrome_track(store, "like-1", title="Like One", artist="A")
    store.upsert_external_track("navidrome", "like-1", track_id)
    client = TestClient(app)

    response = client.get("/navidrome/starred/ids")

    assert response.status_code == 200
    assert response.json() == {
        "user": "alice",
        "count": 2,
        "mapped_count": 1,
        "track_ids": [track_id],
        "item_ids": ["like-1", "ghost"],
        "not_synced_item_ids": ["ghost"],
    }


def test_set_track_navidrome_star_uses_configured_client(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    _configure_navidrome_env(monkeypatch)
    calls: list[tuple[str, str, str]] = []

    class FakeNavidromeClient:
        def __init__(self, settings):
            self.settings = settings

        def star_song(self, item_id: str):
            calls.append(("star", item_id, self.settings.user))

        def unstar_song(self, item_id: str):
            calls.append(("unstar", item_id, self.settings.user))

    monkeypatch.setattr(main_module, "NavidromeClient", FakeNavidromeClient)
    track_id = add_navidrome_track(store, "song-1", title="Song One")
    store.upsert_external_track("navidrome", "song-1", track_id)
    client = TestClient(app)

    starred = client.put(f"/tracks/{track_id}/navidrome-star", json={"starred": True})
    unstarred = client.put(f"/tracks/{track_id}/navidrome-star", json={"starred": False})

    assert starred.status_code == 200
    assert starred.json() == {
        "track_id": track_id,
        "item_id": "song-1",
        "starred": True,
        "user": "alice",
    }
    assert unstarred.status_code == 200
    assert unstarred.json()["starred"] is False
    assert calls == [("star", "song-1", "alice"), ("unstar", "song-1", "alice")]


def test_set_track_navidrome_star_requires_mapping(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    _configure_navidrome_env(monkeypatch)
    track_id = add_track(store, tmp_path / "local.flac")
    client = TestClient(app)

    response = client.put(f"/tracks/{track_id}/navidrome-star", json={"starred": True})

    assert response.status_code == 404
    assert response.json()["detail"] == "Track has no Navidrome mapping"


def test_navidrome_starred_similar_blends_ready_likes(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    _configure_navidrome_env(monkeypatch)

    class FakeNavidromeClient:
        def __init__(self, settings):
            self.settings = settings

        def get_starred_songs(self):
            return [
                NavidromeSong(id="like-1", title="Like One", artist="A"),
                NavidromeSong(id="like-2", title="Like Two", artist="B"),
            ]

    monkeypatch.setattr(main_module, "NavidromeClient", FakeNavidromeClient)
    like1_id = add_navidrome_track(store, "like-1", title="Like One", artist="A")
    like2_id = add_navidrome_track(store, "like-2", title="Like Two", artist="B")
    result_id = add_navidrome_track(store, "result", title="Result", artist="C", album="Other")
    store.upsert_external_track("navidrome", "like-1", like1_id)
    store.upsert_external_track("navidrome", "like-2", like2_id)
    store.upsert_external_track("navidrome", "result", result_id)
    store.save_embedding(like1_id, "discogs_multi", np.array([1.0, 0.0], dtype=np.float32))
    store.save_embedding(like2_id, "discogs_multi", np.array([0.0, 1.0], dtype=np.float32))
    store.save_embedding(result_id, "discogs_multi", np.array([0.7, 0.7], dtype=np.float32))
    main_module.build_index(store, main_module.Settings.from_env(), "discogs_multi")
    client = TestClient(app)

    response = client.get("/navidrome/starred/similar?model=discogs_multi&count=5")

    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "navidrome_starred"
    assert data["blend"] == "average"
    assert data["ready_count"] == 2
    assert data["missing_embedding_count"] == 0
    assert [item["id"] for item in data["results"]] == [result_id]


def test_navidrome_starred_similar_requires_ready_embeddings(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    _configure_navidrome_env(monkeypatch)

    class FakeNavidromeClient:
        def __init__(self, settings):
            self.settings = settings

        def get_starred_songs(self):
            return [NavidromeSong(id="like-1", title="Like One", artist="A")]

    monkeypatch.setattr(main_module, "NavidromeClient", FakeNavidromeClient)
    like_id = add_navidrome_track(store, "like-1", title="Like One", artist="A")
    store.upsert_external_track("navidrome", "like-1", like_id)
    client = TestClient(app)

    response = client.get("/navidrome/starred/similar?model=discogs_multi")

    assert response.status_code == 404
    assert "No ready liked tracks" in response.json()["detail"]


def test_navidrome_similar_returns_external_ids(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    seed_id = add_navidrome_track(store, "seed", title="Seed")
    result_id = add_navidrome_track(store, "result", title="Result", album="Two")
    store.upsert_external_track("navidrome", "seed", seed_id)
    store.upsert_external_track("navidrome", "result", result_id)
    store.save_embedding(seed_id, "discogs_multi", np.array([1.0, 0.0], dtype=np.float32))
    store.save_embedding(result_id, "discogs_multi", np.array([0.9, 0.1], dtype=np.float32))
    main_module.build_index(store, main_module.Settings.from_env(), "discogs_multi")
    client = TestClient(app)

    response = client.get("/navidrome/similar?item_id=seed&model=discogs_multi&count=1")

    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "navidrome"
    assert data["seed_item_id"] == "seed"
    assert data["model"] == "discogs_multi"
    assert data["results"][0]["item_id"] == "result"
    assert data["results"][0]["track_id"] == result_id
    assert data["results"][0]["title"] == "Result"
    assert data["results"][0]["similarity"] > 0


def test_navidrome_similar_uses_instant_mix_settings_for_count_and_history(
    tmp_path: Path,
    monkeypatch,
):
    store = init_api_store(tmp_path, monkeypatch)
    seed_id = add_navidrome_track(store, "seed", title="Seed", album="One")
    first_id = add_navidrome_track(store, "first", title="First", album="Two")
    second_id = add_navidrome_track(store, "second", title="Second", album="Three")
    third_id = add_navidrome_track(store, "third", title="Third", album="Four")
    for external_id, track_id in [
        ("seed", seed_id),
        ("first", first_id),
        ("second", second_id),
        ("third", third_id),
    ]:
        store.upsert_external_track("navidrome", external_id, track_id)
    store.save_embedding(seed_id, "discogs_multi", np.array([1.0, 0.0], dtype=np.float32))
    store.save_embedding(first_id, "discogs_multi", np.array([0.99, 0.01], dtype=np.float32))
    store.save_embedding(second_id, "discogs_multi", np.array([0.98, 0.02], dtype=np.float32))
    store.save_embedding(third_id, "discogs_multi", np.array([0.7, 0.3], dtype=np.float32))
    main_module.build_index(store, main_module.Settings.from_env(), "discogs_multi")
    client = TestClient(app)
    settings_response = client.put(
        "/instant-mix/settings",
        json={
            "count": 2,
            "min_similarity": 0.0,
            "max_per_artist": 10,
            "exclude_same_album": False,
            "count_collaboration_artists": False,
        },
    )
    assert settings_response.status_code == 200
    assert settings_response.json()["count_collaboration_artists"] is False

    response = client.get("/navidrome/similar?item_id=seed&model=discogs_multi&count=100")

    assert response.status_code == 200
    data = response.json()
    assert data["requested_count"] == 100
    assert data["effective_count"] == 2
    assert [item["item_id"] for item in data["results"]] == ["first", "second"]
    assert data["results"][0]["similarity"] >= data["results"][1]["similarity"]
    web_response = client.get(
        f"/tracks/{seed_id}/similar?model=discogs_multi&k=2&max_per_artist=10&exclude_same_album=false"
    )
    assert web_response.status_code == 200
    assert [item["track_id"] for item in data["results"]] == [
        item["id"] for item in web_response.json()["results"]
    ]
    history_response = client.get("/instant-mix/requests")
    assert history_response.status_code == 200
    history = history_response.json()["results"]
    assert history[0]["id"] == data["request_id"]
    assert history[0]["requested_count"] == 100
    assert history[0]["effective_count"] == 2
    detail_response = client.get(f"/instant-mix/requests/{data['request_id']}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["params"]["count_collaboration_artists"] is False
    assert [item["item_id"] for item in detail["results"]] == ["first", "second"]


def test_navidrome_similar_uses_saved_instant_mix_model(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    seed_id = add_navidrome_track(store, "seed", title="Seed", album="One")
    result_id = add_navidrome_track(store, "result", title="Result", album="Two")
    store.upsert_external_track("navidrome", "seed", seed_id)
    store.upsert_external_track("navidrome", "result", result_id)
    store.save_embedding(seed_id, "muq_mulan", np.array([1.0, 0.0], dtype=np.float32))
    store.save_embedding(result_id, "muq_mulan", np.array([0.9, 0.1], dtype=np.float32))
    main_module.build_index(store, main_module.Settings.from_env(), "muq_mulan")
    client = TestClient(app)
    settings_response = client.put(
        "/instant-mix/settings",
        json={
            "model": "muq_mulan",
            "count": 1,
            "min_similarity": 0.0,
            "max_per_artist": 10,
            "exclude_same_album": False,
            "count_collaboration_artists": True,
        },
    )
    assert settings_response.status_code == 200

    response = client.get("/navidrome/similar?item_id=seed&model=discogs_multi")

    assert response.status_code == 200
    data = response.json()
    assert data["model"] == "muq_mulan"
    assert [item["item_id"] for item in data["results"]] == ["result"]
    detail_response = client.get(f"/instant-mix/requests/{data['request_id']}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["params"]["requested_model"] == "discogs_multi"
    assert detail["params"]["effective_model"] == "muq_mulan"


def test_instant_mix_requests_database_error_returns_json(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)

    def fail_list(self, limit=50, offset=0):
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(store_module.Store, "list_instant_mix_requests", fail_list)
    client = TestClient(app)

    response = client.get("/instant-mix/requests?limit=100")

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    assert "Instant mix history could not be read" in response.json()["detail"]


def test_navidrome_similar_filters_by_instant_mix_similarity_threshold(
    tmp_path: Path,
    monkeypatch,
):
    store = init_api_store(tmp_path, monkeypatch)
    seed_id = add_navidrome_track(store, "seed", title="Seed", album="One")
    close_id = add_navidrome_track(store, "close", title="Close", album="Two")
    far_id = add_navidrome_track(store, "far", title="Far", album="Three")
    store.upsert_external_track("navidrome", "seed", seed_id)
    store.upsert_external_track("navidrome", "close", close_id)
    store.upsert_external_track("navidrome", "far", far_id)
    store.save_embedding(seed_id, "discogs_multi", np.array([1.0, 0.0], dtype=np.float32))
    store.save_embedding(close_id, "discogs_multi", np.array([0.99, 0.01], dtype=np.float32))
    store.save_embedding(far_id, "discogs_multi", np.array([0.4, 0.6], dtype=np.float32))
    main_module.build_index(store, main_module.Settings.from_env(), "discogs_multi")
    client = TestClient(app)
    settings_response = client.put(
        "/instant-mix/settings",
        json={
            "count": 50,
            "min_similarity": 0.9,
            "max_per_artist": 10,
            "exclude_same_album": False,
        },
    )
    assert settings_response.status_code == 200

    response = client.get("/navidrome/similar?item_id=seed&model=discogs_multi")

    assert response.status_code == 200
    data = response.json()
    assert [item["item_id"] for item in data["results"]] == ["close"]
    assert data["min_similarity"] == 0.9


def test_navidrome_similar_rejects_unsynced_seed(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.get("/navidrome/similar?item_id=missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "Navidrome item_id is not synced"


def test_navidrome_similar_reports_missing_index(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    seed_id = add_navidrome_track(store, "seed", title="Seed")
    store.upsert_external_track("navidrome", "seed", seed_id)
    store.save_embedding(seed_id, "discogs_multi", np.array([1.0, 0.0], dtype=np.float32))
    client = TestClient(app)

    response = client.get("/navidrome/similar?item_id=seed&model=discogs_multi")

    assert response.status_code == 503
    assert "Index not found" in response.json()["detail"]


def test_navidrome_similar_reports_missing_seed_embedding(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    seed_id = add_navidrome_track(store, "seed", title="Seed")
    result_id = add_navidrome_track(store, "result", title="Result", album="Two")
    store.upsert_external_track("navidrome", "seed", seed_id)
    store.upsert_external_track("navidrome", "result", result_id)
    store.save_embedding(result_id, "discogs_multi", np.array([0.9, 0.1], dtype=np.float32))
    main_module.build_index(store, main_module.Settings.from_env(), "discogs_multi")
    client = TestClient(app)

    response = client.get("/navidrome/similar?item_id=seed&model=discogs_multi")

    assert response.status_code == 404
    assert "No embedding for track" in response.json()["detail"]


def test_navidrome_similar_skips_results_without_external_id(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    seed_id = add_navidrome_track(store, "seed", title="Seed")
    mapped_id = add_navidrome_track(store, "mapped", title="Mapped", album="Two")
    local_path = tmp_path / "local.flac"
    local_path.write_bytes(b"fake")
    local_id = add_track(store, local_path, title="Local", album="Three")
    store.upsert_external_track("navidrome", "seed", seed_id)
    store.upsert_external_track("navidrome", "mapped", mapped_id)
    store.save_embedding(seed_id, "discogs_multi", np.array([1.0, 0.0], dtype=np.float32))
    store.save_embedding(local_id, "discogs_multi", np.array([0.99, 0.01], dtype=np.float32))
    store.save_embedding(mapped_id, "discogs_multi", np.array([0.9, 0.1], dtype=np.float32))
    main_module.build_index(store, main_module.Settings.from_env(), "discogs_multi")
    client = TestClient(app)

    response = client.get("/navidrome/similar?item_id=seed&model=discogs_multi&count=2")

    assert response.status_code == 200
    assert [item["item_id"] for item in response.json()["results"]] == ["mapped"]


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
    result = main_module._extract_embedding_local(
        FakeEmbedder(None, "discogs_multi"),
        store,
        main_module.Settings.from_env(),
        Track(
            id=99,
            path=str(fail_path),
            artist=None,
            title="Fail",
            album=None,
            duration=123.0,
            file_size=1,
            mtime=1,
        ),
    )
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
    settings = main_module.Settings.from_env()
    metadata_path = main_module.index_metadata_path(settings.index_path("discogs_multi"))
    assert settings.index_path("discogs_multi").exists()
    assert json.loads(metadata_path.read_text(encoding="utf-8"))["count"] == 2


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


def test_analyze_audio_features_job_accepts_workers(tmp_path: Path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.post(
        "/jobs/analyze-audio-features",
        json={"limit": 1, "workers": 2},
    )

    assert response.status_code == 200
    assert response.json()["limit"] == 1
    assert response.json()["workers"] == 2


def test_analyze_audio_features_job_saves_successes_with_multiple_workers(tmp_path: Path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    first_path = tmp_path / "first.flac"
    second_path = tmp_path / "second.flac"
    for path in [first_path, second_path]:
        path.write_bytes(b"fake")
    first_id = add_track(store, first_path, title="First")
    second_id = add_track(store, second_path, title="Second")

    class FakeAudioFeatureAnalyzer:
        def analyze_track(self, path: Path):
            return [
                TrackFeature(
                    name="bpm",
                    value=128.0 if path.stem == "first" else 129.0,
                    unit="bpm",
                    confidence=0.9,
                    extractor=AUDIO_FEATURE_EXTRACTOR,
                )
            ]

    class FakeFuture:
        def __init__(self, result):
            self._result = result

        def result(self):
            return self._result

    class FakeExecutor:
        max_workers = None

        def __init__(self, max_workers, initializer, mp_context):
            self.max_workers = max_workers
            self.mp_context = mp_context
            initializer()
            FakeExecutor.max_workers = max_workers

        def submit(self, fn, *args):
            return FakeFuture(fn(*args))

        def shutdown(self, wait=True, cancel_futures=False):
            pass

    monkeypatch.setattr(main_module, "AudioFeatureAnalyzer", FakeAudioFeatureAnalyzer)
    monkeypatch.setattr(main_module, "ProcessPoolExecutor", FakeExecutor)
    monkeypatch.setattr(main_module, "as_completed", lambda futures: futures)
    job_id = main_module.create_job("analyze-audio-features", "test")

    main_module._analyze_audio_features_job(job_id, None, workers=2)

    assert FakeExecutor.max_workers == 2
    assert store.count_feature_tracks(AUDIO_FEATURE_EXTRACTOR) == 2
    assert store.load_features(first_id, AUDIO_FEATURE_EXTRACTOR)[0].value == 128.0
    assert store.load_features(second_id, AUDIO_FEATURE_EXTRACTOR)[0].value == 129.0
    job = main_module.JOBS[job_id]
    assert job.status == "completed"
    assert job.done == 2
    assert job.failed == 0


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
