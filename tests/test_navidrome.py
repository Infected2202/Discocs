from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

from typer.testing import CliRunner

from app.cli import cli
from app.config import NavidromeSettings
from app.navidrome import NavidromeClient, NavidromeSong, songs_from_starred_payload


class FakeResponse:
    def __init__(
        self,
        payload: bytes,
        *,
        headers: dict[str, str] | None = None,
    ):
        self.payload = payload
        self.headers = headers or {}

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def settings(**overrides: object) -> NavidromeSettings:
    values = {
        "url": "http://navidrome:4533",
        "user": "alice",
        "password": "secret",
    }
    values.update(overrides)
    return NavidromeSettings(**values)


def test_token_auth_params_include_subsonic_token(monkeypatch):
    monkeypatch.setattr("app.navidrome.secrets.token_hex", lambda _size: "salt123")
    client = NavidromeClient(settings())

    params = client.auth_params()

    assert params["u"] == "alice"
    assert params["s"] == "salt123"
    assert params["t"] == hashlib.md5(b"secretsalt123").hexdigest()
    assert params["v"] == "1.16.1"
    assert params["c"] == "discocs"
    assert params["f"] == "json"


def test_list_songs_calls_search3_and_parses_song_list():
    seen_urls: list[str] = []

    def opener(request, timeout):
        seen_urls.append(request.full_url)
        return FakeResponse(
            b"""
            {
              "subsonic-response": {
                "status": "ok",
                "searchResult3": {
                  "song": [
                    {
                      "id": "song-1",
                      "title": "Track One",
                      "artist": "Artist",
                      "album": "Album",
                      "duration": 321,
                      "size": 12345,
                      "suffix": "flac",
                      "contentType": "audio/flac"
                    }
                  ]
                }
              }
            }
            """
        )

    client = NavidromeClient(settings(auth_mode="plain"), opener=opener)

    songs = client.list_songs(limit=25, offset=50, query="acid")

    assert songs == [
        NavidromeSong(
            id="song-1",
            title="Track One",
            artist="Artist",
            album="Album",
            duration=321,
            size=12345,
            suffix="flac",
            content_type="audio/flac",
            raw={
                "id": "song-1",
                "title": "Track One",
                "artist": "Artist",
                "album": "Album",
                "duration": 321,
                "size": 12345,
                "suffix": "flac",
                "contentType": "audio/flac",
            },
        )
    ]
    parsed = urlparse(seen_urls[0])
    assert parsed.path == "/rest/search3.view"
    query = parse_qs(parsed.query)
    assert query["query"] == ["acid"]
    assert query["songCount"] == ["25"]
    assert query["songOffset"] == ["50"]
    assert query["p"] == ["secret"]


def test_download_track_falls_back_to_stream(tmp_path: Path):
    seen_paths: list[str] = []

    def opener(request, timeout):
        parsed = urlparse(request.full_url)
        seen_paths.append(parsed.path)
        if parsed.path.endswith("/download.view"):
            raise HTTPError(request.full_url, 403, "Forbidden", {}, None)
        return FakeResponse(
            b"audio bytes",
            headers={
                "Content-Type": "audio/flac",
                "Content-Disposition": 'attachment; filename="track.flac"',
            },
        )

    client = NavidromeClient(settings(auth_mode="plain"), opener=opener)

    downloaded = client.download_track("song:one", tmp_path)

    assert seen_paths == ["/rest/download.view", "/rest/stream.view"]
    assert downloaded.mode == "stream"
    assert downloaded.content_type == "audio/flac"
    assert downloaded.bytes_written == len(b"audio bytes")
    assert downloaded.path == tmp_path / "song_one.flac"
    assert downloaded.path.read_bytes() == b"audio bytes"


def test_get_cover_art_calls_navidrome_api():
    seen_urls: list[str] = []

    def opener(request, timeout):
        seen_urls.append(request.full_url)
        return FakeResponse(
            b"image bytes",
            headers={"Content-Type": "image/jpeg"},
        )

    client = NavidromeClient(settings(auth_mode="plain"), opener=opener)

    cover = client.get_cover_art("cover-1", size=128)

    assert cover.payload == b"image bytes"
    assert cover.content_type == "image/jpeg"
    parsed = urlparse(seen_urls[0])
    assert parsed.path == "/rest/getCoverArt.view"
    query = parse_qs(parsed.query)
    assert query["id"] == ["cover-1"]
    assert query["size"] == ["128"]
    assert query["p"] == ["secret"]


def test_cli_navidrome_list_uses_client(monkeypatch):
    class FakeClient:
        def list_songs(self, *, limit: int, offset: int, query: str):
            assert limit == 2
            assert offset == 3
            assert query == "dub"
            return [
                NavidromeSong(
                    id="song-1",
                    title="Low Pass",
                    artist="Mixer",
                    album="Filters",
                    duration=120,
                    suffix="opus",
                )
            ]

    monkeypatch.setattr("app.cli.get_navidrome_client", lambda: FakeClient())

    result = CliRunner().invoke(
        cli,
        ["navidrome-list", "--limit", "2", "--offset", "3", "--query", "dub"],
    )

    assert result.exit_code == 0
    assert "songs=1 offset=3 query='dub'" in result.stdout
    assert "song-1  Mixer - Low Pass album=Filters duration=120 suffix=opus" in result.stdout


def test_songs_from_starred_payload_prefers_starred2():
    payload = {
        "starred2": {
            "song": [
                {"id": "song-1", "title": "Liked One", "artist": "Artist", "album": "Album"},
            ]
        }
    }

    songs = songs_from_starred_payload(payload)

    assert len(songs) == 1
    assert songs[0].id == "song-1"
    assert songs[0].title == "Liked One"


def test_songs_from_starred_payload_falls_back_to_starred():
    payload = {
        "starred": {
            "song": {"id": "song-2", "title": "Older", "artist": "Artist"}
        }
    }

    songs = songs_from_starred_payload(payload)

    assert len(songs) == 1
    assert songs[0].id == "song-2"


def test_get_starred_songs_calls_get_starred2():
    seen_paths: list[str] = []

    def opener(request, timeout):
        seen_paths.append(urlparse(request.full_url).path)
        return FakeResponse(
            b"""
            {
              "subsonic-response": {
                "status": "ok",
                "starred2": {
                  "song": [{"id": "song-1", "title": "Star", "artist": "A"}]
                }
              }
            }
            """
        )

    client = NavidromeClient(settings(auth_mode="plain"), opener=opener)

    songs = client.get_starred_songs()

    assert seen_paths == ["/rest/getStarred2.view"]
    assert songs[0].id == "song-1"
