from __future__ import annotations

from pathlib import Path

from app.audio_source import navidrome_item_id_from_path, track_audio_path
from app.config import Settings
from app.navidrome import DownloadedTrack
from app.scanner import ScannedTrack
from app.store import Store


class FakeNavidromeClient:
    def __init__(self, settings):
        self.settings = settings

    def download_track(self, item_id: str, target_dir: Path, *, suffix: str | None = None):
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{item_id}{suffix or '.audio'}"
        path.write_bytes(b"downloaded-audio")
        return DownloadedTrack(
            path=path,
            bytes_written=len(b"downloaded-audio"),
            content_type="audio/flac",
            mode="download",
        )


def test_navidrome_item_id_from_path():
    assert navidrome_item_id_from_path("navidrome://song-1") == "song-1"
    assert navidrome_item_id_from_path("navidrome://song%201") == "song 1"
    assert navidrome_item_id_from_path("/music/song.flac") is None


def test_track_audio_path_downloads_and_cleans_navidrome_temp_file(tmp_path, monkeypatch):
    monkeypatch.setattr("app.audio_source.NavidromeClient", FakeNavidromeClient)
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    store = Store(tmp_path / "app.db")
    store.init()
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

    with track_audio_path(store, Settings.from_env(), track) as path:
        assert path.read_bytes() == b"downloaded-audio"
        assert path.exists()
        downloaded_path = path

    assert not downloaded_path.exists()


def test_track_audio_path_keeps_local_path(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    local_path = tmp_path / "track.flac"
    local_path.write_bytes(b"local-audio")
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=local_path,
            artist="Artist",
            title="Title",
            album="Album",
            duration=123.0,
            file_size=len(b"local-audio"),
            mtime=1,
        )
    )
    track = store.get_track(track_id)

    with track_audio_path(store, Settings.from_env(), track) as path:
        assert path == local_path
        assert path.read_bytes() == b"local-audio"

    assert local_path.exists()
