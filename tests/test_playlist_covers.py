"""Collage cover tests for playlists (plans/playlist.md, phase 2)."""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

import app.mix_covers as mix_covers
from app.config import NavidromeSettings, Settings
from app.scanner import ScannedTrack
from app.store import Store


TILE_COLORS = {
    "red": (255, 0, 0),
    "green": (0, 200, 0),
    "blue": (0, 0, 255),
    "white": (255, 255, 255),
}
BACKGROUND = (17, 21, 24)  # #111518
QUADRANT_CENTERS = [(150, 150), (450, 150), (150, 450), (450, 450)]


class FakeNavidromeClient:
    def __init__(self, settings):
        pass

    def get_cover_art(self, cover_art_id: str, size: int | None = None):
        image = Image.new("RGB", (64, 64), TILE_COLORS[cover_art_id])
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return SimpleNamespace(payload=buffer.getvalue())


def app_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        db_path=tmp_path / "app.db",
        model_dir=tmp_path / "models",
        index_dir=tmp_path,
        navidrome=NavidromeSettings(url="http://navidrome.test", user="u", password="p"),
    )


def make_store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "app.db")
    store.init()
    return store


def add_track_with_cover(store: Store, tmp_path: Path, name: str, cover_art_id: str) -> int:
    path = tmp_path / f"{name}.flac"
    path.write_bytes(b"fake")
    stat = path.stat()
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=path,
            artist=f"Artist {name}",
            title=name,
            album="Album",
            duration=180.0,
            file_size=stat.st_size,
            mtime=int(stat.st_mtime),
        )
    )
    store.upsert_external_track(
        "navidrome",
        f"nd-{name}",
        track_id,
        raw_json=json.dumps({"coverArt": cover_art_id}),
    )
    return track_id


def assert_pixel(image: Image.Image, xy: tuple[int, int], expected: tuple[int, int, int]) -> None:
    actual = image.getpixel(xy)
    assert all(abs(a - e) <= 25 for a, e in zip(actual, expected)), (xy, actual, expected)


def test_playlist_cover_four_tracks_fills_grid(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(mix_covers, "NavidromeClient", FakeNavidromeClient)
    store = make_store(tmp_path)
    settings = app_settings(tmp_path)
    tracks = [
        add_track_with_cover(store, tmp_path, name, color)
        for name, color in [("a", "red"), ("b", "green"), ("c", "blue"), ("d", "white")]
    ]

    cover_path = mix_covers.generate_playlist_cover(store, settings, 7, tracks)

    assert cover_path == tmp_path / "playlist_covers" / "7.jpg"
    with Image.open(cover_path) as image:
        assert image.size == (600, 600)
        for center, color in zip(QUADRANT_CENTERS, ["red", "green", "blue", "white"]):
            assert_pixel(image, center, TILE_COLORS[color])


def test_playlist_cover_missing_tiles_stay_dark(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(mix_covers, "NavidromeClient", FakeNavidromeClient)
    store = make_store(tmp_path)
    settings = app_settings(tmp_path)
    track = add_track_with_cover(store, tmp_path, "solo", "red")

    cover_path = mix_covers.generate_playlist_cover(store, settings, 8, [track])

    with Image.open(cover_path) as image:
        assert_pixel(image, QUADRANT_CENTERS[0], TILE_COLORS["red"])
        for center in QUADRANT_CENTERS[1:]:
            assert_pixel(image, center, BACKGROUND)


def test_playlist_cover_keeps_duplicate_artwork_but_mix_dedups(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(mix_covers, "NavidromeClient", FakeNavidromeClient)
    store = make_store(tmp_path)
    settings = app_settings(tmp_path)
    first = add_track_with_cover(store, tmp_path, "one", "red")
    second = add_track_with_cover(store, tmp_path, "two", "red")

    playlist_cover = mix_covers.generate_playlist_cover(store, settings, 9, [first, second])
    with Image.open(playlist_cover) as image:
        assert_pixel(image, QUADRANT_CENTERS[0], TILE_COLORS["red"])
        assert_pixel(image, QUADRANT_CENTERS[1], TILE_COLORS["red"])

    mix_cover = mix_covers.generate_mix_cover(store, settings, "mix-dup", [first, second])
    with Image.open(mix_cover) as image:
        assert_pixel(image, QUADRANT_CENTERS[0], TILE_COLORS["red"])
        assert_pixel(image, QUADRANT_CENTERS[1], BACKGROUND)


def test_refresh_playlist_cover_lifecycle(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(mix_covers, "NavidromeClient", FakeNavidromeClient)
    store = make_store(tmp_path)
    settings = app_settings(tmp_path)
    tracks = [
        add_track_with_cover(store, tmp_path, "x", "red"),
        add_track_with_cover(store, tmp_path, "y", "green"),
    ]
    playlist = store.create_playlist(title="Covered", track_ids=tracks)

    stored_path = mix_covers.refresh_playlist_cover(store, settings, playlist.id)

    assert stored_path is not None
    assert store.get_playlist(playlist.id).cover_path == stored_path
    assert Path(stored_path).is_file()

    # Generation failure (Navidrome unconfigured) keeps the previous cover.
    broken = app_settings(tmp_path)
    broken = Settings(
        data_dir=broken.data_dir,
        db_path=broken.db_path,
        model_dir=broken.model_dir,
        index_dir=broken.index_dir,
        navidrome=NavidromeSettings(),
    )
    assert mix_covers.refresh_playlist_cover(store, broken, playlist.id) == stored_path
    assert store.get_playlist(playlist.id).cover_path == stored_path

    # Emptying the playlist drops the cover and the file.
    store.remove_playlist_tracks(playlist.id, tracks)
    assert mix_covers.refresh_playlist_cover(store, settings, playlist.id) is None
    assert store.get_playlist(playlist.id).cover_path is None
    assert not Path(stored_path).exists()

    assert mix_covers.refresh_playlist_cover(store, settings, 4242) is None
