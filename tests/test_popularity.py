"""Популярность Deezer: импорт связей, фоновое обновление rank/fans, сортировки в API."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from app.cli import cli
from app.library import TrackMetadataEnvelope
from app.main import app
from app.popularity import DeezerUnavailable, refresh_deezer_popularity
from app.scanner import ScannedTrack
from app.store import INITIALIZED_DB_PATHS, Store

NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


def init_api_store(tmp_path: Path, monkeypatch) -> Store:
    db_path = tmp_path / "app.db"
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    monkeypatch.setenv("DISCOCS_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.delenv("DISCOCS_NAVIDROME_URL", raising=False)
    monkeypatch.delenv("DISCOCS_NAVIDROME_USER", raising=False)
    monkeypatch.delenv("DISCOCS_NAVIDROME_PASSWORD", raising=False)
    store = Store(db_path)
    store.init()
    return store


def add_track(
    store: Store,
    tmp_path: Path,
    album: str,
    *,
    navidrome_id: str,
    artist: str = "Artist",
    labels: tuple[str, ...] | None = None,
) -> tuple[int, int]:
    """Трек в своей папке → свой релиз и свой ID Navidrome; возвращает (track_id, release_id)."""
    path = tmp_path / "music" / album / "01.flac"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake")
    scanned = ScannedTrack(
        path=path, artist=artist, title=f"{album} track", album=album,
        year=2020, duration=180.0, file_size=4, mtime=1,
    )
    track_id, _changed = store.upsert_track(scanned)
    release_id = store.upsert_normalized_track_sidecars(
        track_id,
        TrackMetadataEnvelope(
            title=scanned.title, artist=artist, album=album, year=2020,
            duration=180.0, path=str(path), record_labels=labels,
        ),
    )
    store.upsert_external_track("navidrome", navidrome_id, track_id)
    return track_id, release_id


def link(navidrome_id: str, album_id: int, track_id: int | None = None, rank: int | None = None) -> dict:
    return {"navidrome_id": navidrome_id, "deezer_album_id": album_id, "deezer_track_id": track_id, "rank": rank}


def track_row(store: Store, track_id: int):
    with store.connect() as conn:
        return conn.execute("SELECT * FROM track_deezer WHERE track_id = ?", (track_id,)).fetchone()


def album_row(store: Store, album_id: int):
    with store.connect() as conn:
        return conn.execute("SELECT * FROM deezer_albums WHERE deezer_album_id = ?", (album_id,)).fetchone()


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

def test_import_links_known_tracks_and_counts_unknown(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    track_id, _ = add_track(store, tmp_path, "A", navidrome_id="nd-a")

    stats = store.import_deezer_links(
        [link("nd-a", 10, 101, 5000), link("nd-missing", 11, 111, 1)],
        [{"deezer_album_id": 10, "fans": 42, "fetched_at": "2026-10-01T00:00:00+00:00"}],
    )

    assert stats == {"tracks": 1, "unknown_tracks": 1, "albums": 1}
    row = track_row(store, track_id)
    assert (row["deezer_track_id"], row["deezer_album_id"], row["rank"]) == (101, 10, 5000)
    assert album_row(store, 10)["fans"] == 42
    assert album_row(store, 11) is None  # трек не нашёлся — альбом не заводим


def test_import_keeps_newer_snapshot_and_existing_rank(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    track_id, _ = add_track(store, tmp_path, "A", navidrome_id="nd-a")
    store.import_deezer_links(
        [link("nd-a", 10, 101, 5000)],
        [{"deezer_album_id": 10, "fans": 42, "fetched_at": "2026-10-03T00:00:00+00:00"}],
    )

    # Повторный импорт со старым кэшем: без rank и со снимком старше текущего.
    store.import_deezer_links(
        [link("nd-a", 10, 101, None)],
        [{"deezer_album_id": 10, "fans": 7, "fetched_at": "2026-09-01T00:00:00+00:00"}],
    )

    assert track_row(store, track_id)["rank"] == 5000
    assert album_row(store, 10)["fans"] == 42


def test_album_without_snapshot_is_refreshed_first(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    add_track(store, tmp_path, "Fresh", navidrome_id="nd-fresh")
    add_track(store, tmp_path, "Old", navidrome_id="nd-old")
    add_track(store, tmp_path, "New", navidrome_id="nd-new")
    store.import_deezer_links(
        [link("nd-fresh", 1), link("nd-old", 2), link("nd-new", 3)],
        [
            {"deezer_album_id": 1, "fans": 1, "fetched_at": "2026-10-03T00:00:00+00:00"},
            {"deezer_album_id": 2, "fans": 1, "fetched_at": "2026-09-01T00:00:00+00:00"},
            {"deezer_album_id": 99, "fans": None, "fetched_at": None},  # ни с одним треком не связан
        ],
    )

    assert store.stale_deezer_albums("2026-09-27T00:00:00+00:00", 10) == [3, 2]


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------

def test_refresh_updates_fans_and_ranks_and_fetches_long_tracklists(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    first, _ = add_track(store, tmp_path, "A", navidrome_id="nd-a")
    second, _ = add_track(store, tmp_path, "B", navidrome_id="nd-b")
    store.import_deezer_links([link("nd-a", 10, 101), link("nd-b", 10, 130)], [])
    calls: list[str] = []

    def fetch(path: str) -> dict:
        calls.append(path)
        if path == "album/10":
            return {"fans": 500, "nb_tracks": 30, "tracks": {"data": [{"id": 101, "rank": 900}]}}
        return {"data": [{"id": 101, "rank": 900}, {"id": 130, "rank": 300}]}

    result = refresh_deezer_popularity(store, limit=10, fetch=fetch, interval=0, now=NOW)

    assert calls == ["album/10", "album/10/tracks?limit=1000"]
    assert (result.refreshed, result.failed, result.tracks_ranked) == (1, 0, 2)
    assert album_row(store, 10)["fans"] == 500
    assert track_row(store, first)["rank"] == 900
    assert track_row(store, second)["rank"] == 300
    assert store.stale_deezer_albums(NOW.isoformat(), 10) == []


def test_refresh_marks_removed_album_and_keeps_old_numbers(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    track_id, _ = add_track(store, tmp_path, "A", navidrome_id="nd-a")
    store.import_deezer_links(
        [link("nd-a", 10, 101, 5000)],
        [{"deezer_album_id": 10, "fans": 42, "fetched_at": "2026-01-01T00:00:00+00:00"}],
    )

    result = refresh_deezer_popularity(
        store, limit=10, interval=0, now=NOW,
        fetch=lambda path: {"error": {"type": "DataException", "message": "no data", "code": 800}},
    )

    assert result.failed == 1
    row = album_row(store, 10)
    assert (row["fans"], row["error"]) == (42, "no data")
    assert track_row(store, track_id)["rank"] == 5000
    assert store.stale_deezer_albums(NOW.isoformat(), 10) == []  # не спрашиваем снова каждый тик


def test_refresh_stops_without_marking_when_deezer_is_unavailable(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    add_track(store, tmp_path, "A", navidrome_id="nd-a")
    add_track(store, tmp_path, "B", navidrome_id="nd-b")
    store.import_deezer_links([link("nd-a", 10), link("nd-b", 11)], [])
    calls: list[str] = []

    def fetch(path: str) -> dict:
        calls.append(path)
        raise DeezerUnavailable("timeout")

    result = refresh_deezer_popularity(store, limit=10, fetch=fetch, interval=0, now=NOW)

    assert calls == ["album/10"]  # дальше не долбим
    assert result.refreshed == 0
    assert store.stale_deezer_albums(NOW.isoformat(), 10) == [10, 11]


def test_refresh_treats_quota_error_as_unavailable(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    add_track(store, tmp_path, "A", navidrome_id="nd-a")
    store.import_deezer_links([link("nd-a", 10)], [])

    result = refresh_deezer_popularity(
        store, limit=10, interval=0, now=NOW,
        fetch=lambda path: {"error": {"type": "Exception", "message": "Quota limit exceeded", "code": 4}},
    )

    assert (result.refreshed, result.failed) == (0, 0)
    assert album_row(store, 10)["error"] is None
    assert store.stale_deezer_albums(NOW.isoformat(), 10) == [10]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_artist_top_tracks_follow_plays_then_deezer_rank(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    low, _ = add_track(store, tmp_path, "Low", navidrome_id="nd-low", artist="Solo")
    high, _ = add_track(store, tmp_path, "High", navidrome_id="nd-high", artist="Solo")
    played, _ = add_track(store, tmp_path, "Played", navidrome_id="nd-played", artist="Solo")
    store.import_deezer_links([link("nd-low", 1, 11, 100), link("nd-high", 2, 22, 900)], [])
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO user_track_preferences (user_id, track_id, play_count, updated_at) VALUES (?, ?, 2, ?)",
            (store.user_id, played, NOW.isoformat()),
        )
        artist_id = conn.execute("SELECT artist_id FROM track_artists WHERE track_id = ?", (low,)).fetchone()[0]

    body = TestClient(app).get(f"/api/v1/artists/{artist_id}/top-tracks").json()

    assert [item["id"] for item in body["items"]] == [played, high, low]
    assert [item["deezer_rank"] for item in body["items"]] == [None, 900, 100]
    assert body["basis"] == "local_playback_deezer_rank"


def test_label_and_discography_sort_by_deezer_fans(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    _, quiet = add_track(store, tmp_path, "Quiet", navidrome_id="nd-q", artist="Band", labels=("Warp",))
    _, loud = add_track(store, tmp_path, "Loud", navidrome_id="nd-l", artist="Band", labels=("Warp",))
    _, unknown = add_track(store, tmp_path, "Unknown", navidrome_id="nd-u", artist="Band", labels=("Warp",))
    store.import_deezer_links(
        [link("nd-q", 1), link("nd-l", 2)],
        [
            {"deezer_album_id": 1, "fans": 10, "fetched_at": NOW.isoformat()},
            {"deezer_album_id": 2, "fans": 5000, "fetched_at": NOW.isoformat()},
        ],
    )
    assert store.release_popularity([quiet, loud, unknown]) == {quiet: 10, loud: 5000}
    client = TestClient(app)

    label_id = store.label_id_by_name("Warp")
    label_body = client.get(f"/api/v1/labels/{label_id}/releases", params={"sort": "popularity"}).json()
    assert [item["id"] for item in label_body["items"]][:2] == [loud, quiet]
    assert label_body["items"][2]["id"] == unknown
    assert [item["deezer_fans"] for item in label_body["items"]] == [5000, 10, None]

    with store.connect() as conn:
        artist_id = conn.execute("SELECT id FROM artists WHERE name = 'Band'").fetchone()[0]
    disco = client.get(f"/api/v1/artists/{artist_id}/discography", params={"sort": "popularity"}).json()
    ordered = [item["id"] for group in disco["groups"] for item in group["items"]]
    assert ordered[:2] == [loud, quiet]


def test_cli_deezer_import_reads_payload(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    track_id, _ = add_track(store, tmp_path, "A", navidrome_id="nd-a")
    payload = tmp_path / "popularity.json"
    payload.write_text(json.dumps({"tracks": [link("nd-a", 10, 101, 77)], "albums": []}), encoding="utf-8")

    result = CliRunner().invoke(cli, ["deezer-import", str(payload)])

    assert result.exit_code == 0, result.output
    assert "tracks=1" in result.output
    assert track_row(store, track_id)["rank"] == 77
