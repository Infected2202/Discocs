from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from app.navidrome import NavidromeClient

from tests.test_navidrome import FakeResponse, settings


_ALBUM_LIST = b"""
{
  "subsonic-response": {
    "status": "ok",
    "albumList2": {
      "album": [
        {"id": "album-1"},
        {"id": "album-2"}
      ]
    }
  }
}
"""

_ALBUM_1 = b"""
{
  "subsonic-response": {
    "status": "ok",
    "album": {
      "id": "album-1",
      "song": [
        {"id": "song-1", "title": "One", "playCount": 4, "played": "2026-06-30T10:00:00Z"}
      ]
    }
  }
}
"""

_ALBUM_2 = b"""
{
  "subsonic-response": {
    "status": "ok",
    "album": {
      "id": "album-2",
      "song": [
        {"id": "song-2", "title": "Two", "playCount": 1}
      ]
    }
  }
}
"""


def test_iter_recent_played_songs_expands_recent_albums():
    seen_paths: list[str] = []

    def opener(request, timeout):
        parsed = urlparse(request.full_url)
        seen_paths.append(parsed.path)
        if parsed.path.endswith("getAlbumList2.view"):
            query = parse_qs(parsed.query)
            assert query["type"] == ["recent"]
            assert query["size"] == ["2"]
            return FakeResponse(_ALBUM_LIST)
        query = parse_qs(parsed.query)
        return FakeResponse(_ALBUM_1 if query["id"] == ["album-1"] else _ALBUM_2)

    client = NavidromeClient(settings(auth_mode="plain"), opener=opener)

    songs = list(client.iter_recent_played_songs(album_count=2))

    assert [s.id for s in songs] == ["song-1", "song-2"]
    assert songs[0].play_count == 4
    assert songs[0].last_played_at == "2026-06-30T10:00:00Z"
    assert seen_paths[0].endswith("getAlbumList2.view")
    assert sum(p.endswith("getAlbum.view") for p in seen_paths) == 2


def test_iter_recent_played_songs_zero_count_makes_no_calls():
    def opener(request, timeout):  # pragma: no cover - must not be called
        raise AssertionError("no request expected for album_count=0")

    client = NavidromeClient(settings(auth_mode="plain"), opener=opener)
    assert list(client.iter_recent_played_songs(album_count=0)) == []
