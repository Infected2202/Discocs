"""Лейблы: извлечение из Navidrome, связь с релизами, сортировка, API, полка дашборда."""
from __future__ import annotations

import base64
import io
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.library import TrackMetadataEnvelope, record_labels_from_raw, release_date_from_raw
from app.main import app
from app.navidrome import NavidromeSong
from app.navidrome_sync import sync_navidrome_catalog
from app.scanner import ScannedTrack
from app.services.labels import PLACEHOLDER_IMAGE
from app.store import INITIALIZED_DB_PATHS, Store


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

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


def add_release(
    store: Store,
    tmp_path: Path,
    album: str,
    labels: tuple[str, ...] | None,
    *,
    artist: str = "Artist",
    year: int | None = 2020,
    release_date: str | None = None,
) -> tuple[int, int]:
    """Трек в своей папке → свой релиз; возвращает (track_id, release_id)."""
    path = tmp_path / "music" / album / "01.flac"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake")
    scanned = ScannedTrack(
        path=path,
        artist=artist,
        title=f"{album} track",
        album=album,
        year=year,
        duration=180.0,
        file_size=4,
        mtime=1,
    )
    track_id, _changed = store.upsert_track(scanned)
    release_id = store.upsert_normalized_track_sidecars(
        track_id,
        TrackMetadataEnvelope(
            title=scanned.title,
            artist=artist,
            album=album,
            year=year,
            duration=180.0,
            path=str(path),
            release_date=release_date,
            record_labels=labels,
        ),
    )
    return track_id, release_id


def png_base64() -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 10, 10)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


# ---------------------------------------------------------------------------
# разбор raw Navidrome
# ---------------------------------------------------------------------------

def test_record_labels_absent_key_means_unknown_not_empty():
    assert record_labels_from_raw({"title": "x"}) is None
    assert record_labels_from_raw({"recordLabels": []}) == ()


def test_record_labels_dedupe_and_skip_no_label_marker():
    raw = {
        "recordLabels": [
            {"name": "Tresor"},
            {"name": " tresor "},
            {"name": "[no label]"},
            {"name": "Tresor Records"},
        ]
    }
    assert record_labels_from_raw(raw) == ("Tresor", "Tresor Records")


def test_release_date_from_opensubsonic_item_date_keeps_known_precision():
    assert release_date_from_raw({"releaseDate": {"year": 2014, "month": 12, "day": 8}}) == "2014-12-08"
    assert release_date_from_raw({"releaseDate": {"year": 2014, "month": 3}}) == "2014-03"
    assert release_date_from_raw({"releaseDate": {"year": 2014}}) == "2014"
    assert release_date_from_raw({"releaseDate": {}}) is None
    assert release_date_from_raw({"releaseDate": "2001-05-01"}) == "2001-05-01"


# ---------------------------------------------------------------------------
# синк Navidrome
# ---------------------------------------------------------------------------

class AlbumNavidromeClient:
    def __init__(self, songs: list[NavidromeSong], albums: list[dict] | None):
        self.songs = songs
        self.albums = albums

    def iter_songs(self, *, page_size: int, query: str = "", limit: int | None = None):
        yield from self.songs

    def iter_albums(self, *, page_size: int = 500):
        if self.albums is None:
            raise RuntimeError("getAlbumList2 unavailable")
        yield from self.albums


def nav_song(item_id: str, album_id: str) -> NavidromeSong:
    return NavidromeSong(
        id=item_id,
        title=f"Song {item_id}",
        artist="Artist",
        album=f"Album {album_id}",
        duration=200,
        size=100,
        suffix="flac",
        year=2014,
        raw={"id": item_id, "title": f"Song {item_id}", "albumId": album_id, "size": 100},
    )


def release_labels(store: Store, release_id: int) -> list[str]:
    return [label.name for label in store.labels_for_release(release_id)]


def test_sync_links_releases_to_album_labels_and_release_date(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    client = AlbumNavidromeClient(
        [nav_song("s1", "al-1"), nav_song("s2", "al-2")],
        [
            {
                "id": "al-1",
                "recordLabels": [{"name": "Trip Recordings"}, {"name": "Warp"}],
                "releaseDate": {"year": 2014, "month": 12, "day": 8},
            },
            {"id": "al-2", "recordLabels": [{"name": "trip recordings"}]},
        ],
    )

    sync_navidrome_catalog(store, client)

    first = store.get_release(store.release_id_for_track(store.get_track_by_external_id("navidrome", "s1").id))
    second_id = store.release_id_for_track(store.get_track_by_external_id("navidrome", "s2").id)
    assert release_labels(store, first.release.id) == ["Trip Recordings", "Warp"]
    assert first.release.label == "Trip Recordings / Warp"
    assert first.release.release_date == "2014-12-08"
    # Одно и то же название в другом регистре — тот же лейбл.
    assert release_labels(store, second_id) == ["Trip Recordings"]
    labels, total = store.list_labels(limit=10, offset=0)
    assert total == 2
    assert [(label.name, label.release_count) for label in labels] == [("Trip Recordings", 2), ("Warp", 1)]


def test_sync_removes_labels_dropped_from_album_but_keeps_them_when_albums_unavailable(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    songs = [nav_song("s1", "al-1")]
    sync_navidrome_catalog(store, AlbumNavidromeClient(songs, [{"id": "al-1", "recordLabels": [{"name": "Warp"}]}]))
    release_id = store.release_id_for_track(store.get_track_by_external_id("navidrome", "s1").id)
    assert release_labels(store, release_id) == ["Warp"]

    # getAlbumList2 упал — про лейблы ничего не известно, связи не трогаем.
    sync_navidrome_catalog(store, AlbumNavidromeClient(songs, None))
    assert release_labels(store, release_id) == ["Warp"]

    # Альбом есть, лейблов у него больше нет — связь снимается.
    sync_navidrome_catalog(store, AlbumNavidromeClient(songs, [{"id": "al-1"}]))
    assert release_labels(store, release_id) == []
    assert store.get_release(release_id).release.label is None


# ---------------------------------------------------------------------------
# store: список и сортировка
# ---------------------------------------------------------------------------

def test_list_labels_orders_by_release_count_and_ignores_missing_releases(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    add_release(store, tmp_path, "A1", ("Small",))
    add_release(store, tmp_path, "B1", ("Big",))
    add_release(store, tmp_path, "B2", ("Big",))
    gone_track, _ = add_release(store, tmp_path, "G1", ("Gone",))
    with store.connect() as conn:
        conn.execute("UPDATE tracks SET missing_at = '2026-01-01' WHERE id = ?", (gone_track,))

    labels, total = store.list_labels(limit=10, offset=0)

    assert [label.name for label in labels] == ["Big", "Small"]
    assert [label.release_count for label in labels] == [2, 1]
    assert total == 2
    page, _ = store.list_labels(limit=1, offset=1)
    assert [label.name for label in page] == ["Small"]


def test_label_releases_sort_by_release_date_with_year_only_by_title(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    add_release(store, tmp_path, "Mid Dated", ("L",), year=2015, release_date="2015-06-01")
    add_release(store, tmp_path, "Zeta", ("L",), year=2015, release_date=None)
    add_release(store, tmp_path, "Alpha", ("L",), year=2015, release_date="2015")
    add_release(store, tmp_path, "Newest", ("L",), year=2020, release_date="2020-01-02")
    add_release(store, tmp_path, "Oldest", ("L",), year=2001, release_date="2001-02")
    add_release(store, tmp_path, "Undated", ("L",), year=None, release_date=None)
    label_id = store.label_id_by_name("L")

    newest = [row.release.title for row in store.label_releases(label_id, newest_first=True)]
    oldest = [row.release.title for row in store.label_releases(label_id, newest_first=False)]

    assert newest == ["Newest", "Mid Dated", "Alpha", "Zeta", "Oldest", "Undated"]
    assert oldest == ["Oldest", "Alpha", "Zeta", "Mid Dated", "Newest", "Undated"]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_labels_api_list_detail_releases_and_not_found(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    add_release(store, tmp_path, "Old", ("Ninja Tune",), year=1999, release_date="1999-01-01")
    add_release(store, tmp_path, "New", ("Ninja Tune",), year=2010, release_date="2010-01-01")
    add_release(store, tmp_path, "Other", ("Warp",))
    label_id = store.label_id_by_name("Ninja Tune")
    client = TestClient(app)

    listing = client.get("/api/v1/labels", params={"limit": 1})
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 2
    assert body["next_offset"] == 1
    assert body["items"][0]["name"] == "Ninja Tune"
    assert body["items"][0]["release_count"] == 2
    assert body["items"][0]["artwork"]["placeholder"] is True

    detail = client.get(f"/api/v1/labels/{label_id}").json()["label"]
    assert detail["name"] == "Ninja Tune"
    assert detail["description"] is None
    assert detail["links"] == []

    desc = client.get(f"/api/v1/labels/{label_id}/releases").json()
    assert [item["title"] for item in desc["items"]] == ["New", "Old"]
    asc = client.get(f"/api/v1/labels/{label_id}/releases", params={"sort": "release_date_asc"}).json()
    assert [item["title"] for item in asc["items"]] == ["Old", "New"]

    assert client.get("/api/v1/labels/999999").status_code == 404
    assert client.get("/api/v1/labels/999999/releases").status_code == 404
    assert client.get(f"/api/v1/labels/{label_id}/releases", params={"sort": "title"}).status_code == 422


def test_label_image_is_placeholder_until_metadata_uploaded(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    add_release(store, tmp_path, "R", ("Warp",))
    label_id = store.label_id_by_name("Warp")
    client = TestClient(app)

    placeholder = client.get(f"/api/v1/labels/{label_id}/image")
    assert placeholder.status_code == 200
    assert placeholder.content == PLACEHOLDER_IMAGE.read_bytes()

    uploaded = client.put(
        "/api/v1/labels/metadata",
        json={"name": "warp", "image_base64": png_base64(), "image_source": "beatport"},
    )
    assert uploaded.status_code == 200
    label = uploaded.json()["label"]
    assert label["id"] == label_id
    assert label["artwork"] == {
        "url": label["artwork"]["url"],
        "source": "beatport",
        "placeholder": False,
    }

    image = client.get(f"/api/v1/labels/{label_id}/image")
    assert image.headers["content-type"] == "image/png"
    assert image.content != PLACEHOLDER_IMAGE.read_bytes()
    assert Image.open(io.BytesIO(image.content)).size == (4, 4)

    # Повторная отправка без картинки не стирает сохранённую.
    client.put("/api/v1/labels/metadata", json={"name": "Warp", "description": "Sheffield"})
    assert client.get(f"/api/v1/labels/{label_id}/image").content == image.content


def test_label_metadata_description_links_and_artist_mentions(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    add_release(store, tmp_path, "R", ("Trip",), artist="Nina Kraviz")
    label_id = store.label_id_by_name("Trip")
    nina_id = store.artist_ids_by_names(["Nina Kraviz"])["nina kraviz"]
    client = TestClient(app)

    response = client.put(
        "/api/v1/labels/metadata",
        json={
            "name": "Trip",
            "description": "Label of [a=Nina Kraviz] with [a=Somebody Else].\r\n\r\n\r\nMoscow.",
            "description_source": "discogs",
            "links": [{"url": "https://trip.bandcamp.com", "title": "Bandcamp"}],
            "external_ids": {"discogs": "771357"},
        },
    )

    assert response.status_code == 200
    label = client.get(f"/api/v1/labels/{label_id}").json()["label"]
    assert label["description"] == {
        "source": "discogs",
        "segments": [
            {"type": "text", "text": "Label of "},
            {"type": "artist", "text": "Nina Kraviz", "artist_id": nina_id},
            {"type": "text", "text": " with "},
            {"type": "artist", "text": "Somebody Else", "artist_id": None},
            {"type": "text", "text": ".\n\nMoscow."},
        ],
    }
    assert label["links"] == [{"url": "https://trip.bandcamp.com", "title": "Bandcamp"}]


def test_label_metadata_rejects_bad_image_and_unsafe_links(tmp_path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    not_image = base64.b64encode(b"definitely not a picture").decode("ascii")
    bad_image = client.put("/api/v1/labels/metadata", json={"name": "X", "image_base64": not_image})
    assert bad_image.status_code == 400
    assert bad_image.json()["error"]["code"] == "invalid_image"

    for broken in ("not*base64!", "картинка"):
        response = client.put("/api/v1/labels/metadata", json={"name": "X", "image_base64": broken})
        assert response.status_code == 400, broken
        assert response.json()["error"]["message"] == "Image is not valid base64"

    unsafe = client.put(
        "/api/v1/labels/metadata",
        json={"name": "X", "links": [{"url": "javascript:alert(1)"}]},
    )
    assert unsafe.status_code == 422


def test_metadata_for_unknown_label_creates_it_without_releases(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.put("/api/v1/labels/metadata", json={"name": "Future Label", "description": "Soon"})

    assert response.status_code == 200
    assert store.label_id_by_name("future label") is not None
    # Без живых релизов лейбл не попадает ни в список, ни на полку.
    assert client.get("/api/v1/labels").json()["total"] == 0


def test_dashboard_labels_shelf_has_label_cards_without_play(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    add_release(store, tmp_path, "R1", ("Warp",))
    add_release(store, tmp_path, "R2", ("Warp",))
    add_release(store, tmp_path, "R3", ("Ninja Tune",))
    client = TestClient(app)

    shelf = client.get("/api/v1/dashboard/shelves/labels").json()

    assert shelf["key"] == "labels"
    assert shelf["total"] == 2
    first = shelf["items"][0]
    assert first["entity_type"] == "label"
    assert first["title"] == "Warp"
    assert first["release_count"] == 2
    assert first["action"]["target"] == f"/labels/{store.label_id_by_name('Warp')}"
    assert first["play_action"] is None
    assert "labels" in client.get("/api/v1/dashboard").json()["settings"]["visible_shelves"]
