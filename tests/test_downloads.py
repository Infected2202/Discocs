from __future__ import annotations

from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
from urllib.error import HTTPError
from zipfile import ZipFile

from fastapi.testclient import TestClient

import app.api.downloads as downloads_api
from app.config import NavidromeSettings
from app.main import app
from app.navidrome import NavidromeClient
from app.downloads import public_download_error, unique_archive_filename
from app.scanner import ScannedTrack
from app.store import INITIALIZED_DB_PATHS, Store


def init_store(tmp_path: Path, monkeypatch) -> Store:
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
    path: Path,
    *,
    artist: str,
    title: str,
    album: str = "Test Album",
    payload: bytes = b"audio",
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    stat = path.stat()
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=path,
            artist=artist,
            title=title,
            album=album,
            duration=120.0,
            file_size=stat.st_size,
            mtime=int(stat.st_mtime),
        )
    )
    return track_id


def open_zip(response) -> ZipFile:
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    assert "content-length" not in response.headers
    return ZipFile(BytesIO(response.content))


def test_track_download_returns_original_bytes_and_attachment_name(tmp_path: Path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    track_id = add_track(
        store,
        tmp_path / "music" / "source.flac",
        artist="Artist",
        title="A/B: Track",
        payload=b"original-flac-bytes",
    )

    response = TestClient(app).get(f"/api/v1/tracks/{track_id}/download")

    assert response.content == b"original-flac-bytes"
    assert response.headers["cache-control"] == "private, no-store"
    assert "attachment" in response.headers["content-disposition"]
    assert "Artist%20-%20A_B_%20Track.flac" in response.headers["content-disposition"]


def test_collection_downloads_stream_valid_ordered_archives(tmp_path: Path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    first = add_track(
        store, tmp_path / "music" / "01.flac",
        artist="Alpha", title="First", payload=b"first",
    )
    second = add_track(
        store, tmp_path / "music" / "02.mp3",
        artist="Beta", title="Second", payload=b"second",
    )
    release_id = store.release_id_for_track(first)
    assert release_id is not None
    playlist = store.create_playlist(title="Road/Trip", track_ids=[second, first])
    store.save_generated_mix(
        mix_id="mix-1",
        title="Evening Mix",
        mix_type="taste_region",
        items=[{"track_id": first, "position": 0}, {"track_id": second, "position": 1}],
    )
    client = TestClient(app)

    with open_zip(client.get(f"/api/v1/releases/{release_id}/download")) as archive:
        names = archive.namelist()
        assert names == ["Test Album/01 - First.flac", "Test Album/02 - Second.mp3"]
        assert archive.read(names[0]) == b"first"
        assert archive.read(names[1]) == b"second"

    with open_zip(client.get(f"/api/v1/playlists/{playlist.id}/download")) as archive:
        names = archive.namelist()
        assert names == [
            "Road_Trip/001 - Beta - Second.mp3",
            "Road_Trip/002 - Alpha - First.flac",
        ]
        assert archive.read(names[0]) == b"second"

    with open_zip(client.get("/api/v1/mixes/mix-1/download")) as archive:
        assert archive.namelist() == [
            "Evening Mix/001 - Alpha - First.flac",
            "Evening Mix/002 - Beta - Second.mp3",
        ]


def test_archive_stays_valid_and_reports_missing_tracks(tmp_path: Path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    missing_path = tmp_path / "music" / "gone.flac"
    track_id = add_track(store, missing_path, artist="Lost", title="Gone")
    missing_path.unlink()
    playlist = store.create_playlist(title="Incomplete", track_ids=[track_id])

    response = TestClient(app).get(f"/api/v1/playlists/{playlist.id}/download")

    with open_zip(response) as archive:
        assert archive.namelist() == ["Incomplete/DOWNLOAD_ERRORS.txt"]
        manifest = archive.read("Incomplete/DOWNLOAD_ERRORS.txt").decode()
        assert "001 - Lost - Gone" in manifest
        assert "not mounted" in manifest
    assert store.get_track(track_id).missing_at is not None


def test_navidrome_track_download_uses_original_download_endpoint(tmp_path: Path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    local_id = add_track(store, tmp_path / "music" / "mapped.flac", artist="Remote", title="Song")
    store.upsert_external_track("navidrome", "song-1", local_id)
    seen: dict[str, object] = {}

    class FakeResponse:
        headers = {"Content-Type": "audio/flac", "Content-Disposition": 'attachment; filename="remote.flac"'}

        def __init__(self):
            self.chunks = [b"remote-", b"bytes", b""]

        def read(self, _size):
            return self.chunks.pop(0)

        def close(self):
            seen["closed"] = True

    def opener(request, timeout):
        seen["path"] = urlparse(request.full_url).path
        seen["timeout"] = timeout
        return FakeResponse()

    nav = NavidromeClient(
        NavidromeSettings(url="http://navidrome:4533", user="u", password="p", auth_mode="plain"),
        opener=opener,
    )
    monkeypatch.setattr(downloads_api, "_navidrome_user_client", lambda _settings: (nav, "u"))

    response = TestClient(app).get(f"/api/v1/tracks/{local_id}/download")

    assert response.status_code == 200
    assert response.content == b"remote-bytes"
    assert seen["path"] == "/rest/download.view"
    assert seen["closed"] is True


def test_download_error_does_not_expose_navidrome_auth_query():
    error = HTTPError(
        "http://navidrome/rest/download.view?id=1&u=user&p=secret",
        403,
        "Forbidden",
        {},
        None,
    )

    message = public_download_error(error)

    assert message == "Navidrome request failed with HTTP 403: Forbidden"
    assert "secret" not in message


def test_duplicate_archive_names_are_made_unique_case_insensitively():
    used: set[str] = set()

    assert unique_archive_filename("Mix/Track.flac", used) == "Mix/Track.flac"
    assert unique_archive_filename("Mix/track.flac", used) == "Mix/track (2).flac"
