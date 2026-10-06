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


def test_editorial_description_survives_label_sync_until_cleared(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    add_release(store, tmp_path, "R", ("Trip",), artist="Nina Kraviz")
    label_id = store.label_id_by_name("Trip")
    client = TestClient(app)
    sync_payload = {
        "name": "Trip",
        "description": "From Wikipedia.",
        "description_source": "wikipedia_en",
        "links": [{"url": "https://trip.bandcamp.com"}],
    }
    client.put("/api/v1/labels/metadata", json=sync_payload)

    response = client.put(
        f"/api/v1/labels/{label_id}/description",
        json={"description": "Лейбл [a=Nina Kraviz].\r\n\r\n\r\nМосква."},
    )

    assert response.status_code == 200
    assert response.json()["label"]["description"]["source"] == "editorial"

    # Повторный label-sync обновляет ссылки, но ручной текст не трогает.
    client.put("/api/v1/labels/metadata", json={**sync_payload, "links": [{"url": "https://ra.co/labels/trip"}]})
    label = client.get(f"/api/v1/labels/{label_id}").json()["label"]
    assert label["description"]["source"] == "editorial"
    assert "".join(segment["text"] for segment in label["description"]["segments"]) == "Лейбл Nina Kraviz.\n\nМосква."
    assert label["links"] == [{"url": "https://ra.co/labels/trip", "title": None}]

    # Пустое описание снимает защиту — следующий label-sync снова пишет своё.
    cleared = client.put(f"/api/v1/labels/{label_id}/description", json={"description": "  "})
    assert cleared.json()["label"]["description"] is None
    client.put("/api/v1/labels/metadata", json=sync_payload)
    label = client.get(f"/api/v1/labels/{label_id}").json()["label"]
    assert label["description"]["source"] == "wikipedia_en"


def test_editorial_description_for_unknown_label_is_not_found(tmp_path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.put("/api/v1/labels/999/description", json={"description": "Text"})

    assert response.status_code == 404
    assert client.put("/api/v1/labels/999/description", json={"text": "x"}).status_code == 422


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


# ---------------------------------------------------------------------------
# лайки, группы по типу, лейблы релиза
# ---------------------------------------------------------------------------

def set_release_type(store: Store, release_id: int, release_type: str) -> None:
    with store.connect() as conn:
        conn.execute("UPDATE releases SET release_type = ? WHERE id = ?", (release_type, release_id))


def test_liked_labels_come_first_then_by_release_count(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    for album in ("B1", "B2", "B3"):
        add_release(store, tmp_path, album, ("Big",))
    add_release(store, tmp_path, "M1", ("Mid",))
    add_release(store, tmp_path, "M2", ("Mid",))
    add_release(store, tmp_path, "S1", ("Small",))
    small_id = store.label_id_by_name("Small")
    client = TestClient(app)

    assert client.put(f"/api/v1/labels/{small_id}/like").json() == {"label_id": small_id, "liked": True}

    names = [item["name"] for item in client.get("/api/v1/labels").json()["items"]]
    assert names == ["Small", "Big", "Mid"]
    shelf = client.get("/api/v1/dashboard/shelves/labels").json()["items"]
    assert [item["title"] for item in shelf] == ["Small", "Big", "Mid"]
    assert client.get(f"/api/v1/labels/{small_id}").json()["label"]["liked"] is True

    # Среди лайкнутых — те же правила: больше релизов выше.
    client.put(f"/api/v1/labels/{store.label_id_by_name('Mid')}/like")
    names = [item["name"] for item in client.get("/api/v1/labels").json()["items"]]
    assert names == ["Mid", "Small", "Big"]

    client.delete(f"/api/v1/labels/{small_id}/like")
    names = [item["name"] for item in client.get("/api/v1/labels").json()["items"]]
    assert names == ["Mid", "Big", "Small"]
    assert client.get(f"/api/v1/labels/{small_id}").json()["label"]["liked"] is False


def test_like_unknown_label_is_not_found(tmp_path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    assert client.put("/api/v1/labels/999999/like").status_code == 404
    assert client.delete("/api/v1/labels/999999/like").status_code == 404


def test_service_token_lists_labels_without_likes_and_cannot_like(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    add_release(store, tmp_path, "R", ("Warp",))
    label_id = store.label_id_by_name("Warp")
    monkeypatch.setenv("DISCOCS_AUTH_ENABLED", "true")
    monkeypatch.setenv("DISCOCS_NAVIDROME_URL", "http://navidrome:4533")
    monkeypatch.setenv("DISCOCS_SERVICE_TOKEN", "svc-secret")
    headers = {"X-Discocs-Service-Token": "svc-secret"}
    client = TestClient(app)

    # label-sync ходит с сервисным токеном: пользователя нет, но список и запись метаданных работают.
    listing = client.get("/api/v1/labels", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["items"][0]["liked"] is False
    assert client.put("/api/v1/labels/metadata", json={"name": "Warp"}, headers=headers).status_code == 200
    assert client.put(f"/api/v1/labels/{label_id}/like", headers=headers).status_code == 403


def test_label_releases_are_grouped_by_release_type(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    types = {
        "Album Old": ("album", "2001-01-01"),
        "Album New": ("album", "2020-01-01"),
        "Single One": ("single", "2015-01-01"),
        "The EP": ("ep", "2016-01-01"),
        "Soundtrack": ("soundtrack", "2010-01-01"),
    }
    for title, (release_type, date) in types.items():
        _track, release_id = add_release(store, tmp_path, title, ("L",), year=int(date[:4]), release_date=date)
        set_release_type(store, release_id, release_type)
    label_id = store.label_id_by_name("L")
    client = TestClient(app)

    body = client.get(f"/api/v1/labels/{label_id}/releases").json()

    assert [group["key"] for group in body["groups"]] == ["albums", "eps", "singles", "releases"]
    assert [item["title"] for item in body["groups"][0]["items"]] == ["Album New", "Album Old"]
    assert [item["title"] for item in body["groups"][3]["items"]] == ["Soundtrack"]
    asc = client.get(f"/api/v1/labels/{label_id}/releases", params={"sort": "release_date_asc"}).json()
    assert [item["title"] for item in asc["groups"][0]["items"]] == ["Album Old", "Album New"]
    # Плоский список для tools/label-sync остаётся.
    assert len(body["items"]) == 5


def test_release_detail_lists_its_labels_in_tag_order(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    _track, release_id = add_release(store, tmp_path, "Both", ("Warp", "Bleep"))
    _other, plain_id = add_release(store, tmp_path, "Plain", None)
    client = TestClient(app)

    labels = client.get(f"/api/v1/releases/{release_id}").json()["release"]["labels"]

    assert labels == [
        {"id": store.label_id_by_name("Warp"), "name": "Warp"},
        {"id": store.label_id_by_name("Bleep"), "name": "Bleep"},
    ]
    assert client.get(f"/api/v1/releases/{plain_id}").json()["release"]["labels"] == []


def test_artist_labels_count_the_artists_own_releases_and_skip_missing_ones(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    _t, first_id = add_release(store, tmp_path, "One", ("Bleep",), artist="Synth Unit")
    add_release(store, tmp_path, "Two", ("Warp", "Bleep"), artist="Synth Unit")
    add_release(store, tmp_path, "Three", ("Warp",), artist="Synth Unit")
    add_release(store, tmp_path, "Four", ("Warp",), artist="Synth Unit")
    gone_track, _ = add_release(store, tmp_path, "Gone", ("Gone Records",), artist="Synth Unit")
    add_release(store, tmp_path, "Other", ("Ninja Tune", "Warp"), artist="Someone Else")
    with store.connect() as conn:
        conn.execute("UPDATE tracks SET missing_at = '2026-01-01' WHERE id = ?", (gone_track,))
    client = TestClient(app)
    artist_id = client.get(f"/api/v1/releases/{first_id}").json()["release"]["artists"][0]["id"]

    data = client.get(f"/api/v1/artists/{artist_id}/labels").json()

    # Больше релизов артиста — выше (Warp 3, Bleep 2); подпись карточки — все релизы лейбла,
    # как везде (у Warp ещё релиз Someone Else). Ninja Tune — не его лейбл, Gone Records — без файлов.
    assert [(item["name"], item["release_count"]) for item in data["items"]] == [("Warp", 4), ("Bleep", 2)]
    assert data["total"] == 2
    page = client.get(f"/api/v1/artists/{artist_id}/labels", params={"limit": 1, "offset": 1}).json()
    assert [item["name"] for item in page["items"]] == ["Bleep"]
    assert (page["total"], page["next_offset"]) == (2, None)
    assert client.get(f"/api/v1/artists/{artist_id}/labels", params={"limit": 1}).json()["next_offset"] == 1
    assert data["items"][0]["artwork"]["url"].startswith(f"/api/v1/labels/{store.label_id_by_name('Warp')}/image")
    assert client.get(f"/api/v1/artists/{artist_id}").json()["links"]["labels"] == f"/api/v1/artists/{artist_id}/labels"
    assert client.get("/api/v1/artists/999999/labels").status_code == 404


# ---------------------------------------------------------------------------
# воспроизведение лейбла (кнопка «Перемешать»)
# ---------------------------------------------------------------------------

def add_label_track(
    store: Store,
    tmp_path: Path,
    album: str,
    number: int,
    *,
    labels: tuple[str, ...] = ("L",),
    release_date: str = "2020-01-01",
) -> int:
    """Трек номер ``number`` релиза ``album`` (одна папка — один релиз)."""
    path = tmp_path / "music" / album / f"{number:02d}.flac"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake")
    scanned = ScannedTrack(
        path=path, artist="Artist", title=f"{album} {number}", album=album,
        year=int(release_date[:4]), duration=180.0, file_size=4, mtime=1,
    )
    track_id, _changed = store.upsert_track(scanned)
    store.upsert_normalized_track_sidecars(
        track_id,
        TrackMetadataEnvelope(
            title=scanned.title, artist="Artist", album=album, year=scanned.year,
            duration=180.0, path=str(path), track_number=number,
            release_date=release_date, record_labels=labels,
        ),
    )
    return track_id


def test_label_track_ids_go_newest_release_first_in_track_order(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    old_2 = add_label_track(store, tmp_path, "Old", 2, release_date="2001-01-01")
    old_1 = add_label_track(store, tmp_path, "Old", 1, release_date="2001-01-01")
    new_1 = add_label_track(store, tmp_path, "New", 1, release_date="2020-01-01")
    gone = add_label_track(store, tmp_path, "New", 2, release_date="2020-01-01")
    add_label_track(store, tmp_path, "Elsewhere", 1, labels=("Other",))
    with store.connect() as conn:
        conn.execute("UPDATE tracks SET missing_at = '2026-01-01' WHERE id = ?", (gone,))
    label_id = store.label_id_by_name("L")

    assert store.label_track_ids(label_id, limit=10) == [new_1, old_1, old_2]
    assert store.label_track_ids(label_id, limit=2) == [new_1, old_1]


def test_label_shuffle_samples_across_the_whole_catalog(tmp_path, monkeypatch):
    store = Store(tmp_path / "app.db")
    store.init()
    track_ids = [add_label_track(store, tmp_path, f"R{n}", 1, release_date=f"20{n:02d}-01-01") for n in range(10)]
    label_id = store.label_id_by_name("L")
    seen_population: list[list[int]] = []

    def fake_sample(population, k):
        seen_population.append(list(population))
        return list(population)[-k:]

    monkeypatch.setattr("app.store.labels.random.sample", fake_sample)

    sample = store.label_track_ids(label_id, limit=3, shuffle=True)

    # Выборка — из всех треков лейбла, а не из первых трёх подряд (самых свежих).
    assert sorted(seen_population[0]) == sorted(track_ids)
    assert sample == seen_population[0][-3:]
    assert not set(sample) & set(store.label_track_ids(label_id, limit=3))


def test_playback_session_from_label_queues_its_tracks(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    first = add_label_track(store, tmp_path, "One", 1)
    second = add_label_track(store, tmp_path, "One", 2)
    label_id = store.label_id_by_name("L")
    client = TestClient(app)

    response = client.post(
        "/api/v1/playback/sessions",
        json={"source_type": "label", "source_id": label_id, "source_label": "L",
              "mode": "shuffle", "shuffle_enabled": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["session"]["source_type"] == "label"
    assert body["session"]["shuffle_enabled"] is True
    assert sorted(item["track_id"] for item in body["queue"]["items"]) == sorted([first, second])


def test_search_finds_labels_by_name_and_old_spelling_exact_match_first(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    add_release(store, tmp_path, "T1", ("Trip Recordings",))
    add_release(store, tmp_path, "T2", ("ТРИП",))
    for album in ("X1", "X2", "X3"):
        add_release(store, tmp_path, album, ("Triptych",))
    gone_track, _ = add_release(store, tmp_path, "G1", ("Trip Gone",))
    add_release(store, tmp_path, "U1", ("Ultra Records",))
    add_release(store, tmp_path, "S1", ("Soul Trader",))
    with store.connect() as conn:
        conn.execute("UPDATE tracks SET missing_at = '2026-01-01' WHERE id = ?", (gone_track,))
    client = TestClient(app)

    groups = {group["type"]: group for group in client.get("/api/v1/search", params={"q": "trip"}).json()["groups"]}

    labels = groups["labels"]
    # «Trip» — это Trip Recordings (ключ склейки), он выше Triptych с бо́льшим числом релизов;
    # лейбл без доступных релизов не показывается.
    assert [(item["name"], item["release_count"]) for item in labels["items"]] == [
        ("Trip Recordings", 2), ("Triptych", 3),
    ]
    assert labels["total"] == 2
    assert labels["items"][0]["artwork"]["placeholder"] is True
    # По прежнему написанию кириллицей — тот же лейбл, хотя в названии его нет.
    by_old_name = client.get("/api/v1/search", params={"q": "трип", "type": "label"}).json()["groups"]
    by_old_name = {group["type"]: group for group in by_old_name}
    assert [item["name"] for item in by_old_name["labels"]["items"]] == ["Trip Recordings"]
    assert by_old_name["releases"]["items"] == []
    page = client.get("/api/v1/search", params={"q": "trip", "type": "label", "limit": 1}).json()["groups"]
    page = {group["type"]: group for group in page}["labels"]
    assert ([item["name"] for item in page["items"]], page["next_offset"]) == (["Trip Recordings"], 1)
    # Ключ склейки без пробелов: «soultrader» содержит «ultra», но это не Ultra.
    ultra = client.get("/api/v1/search", params={"q": "ultra", "type": "label"}).json()["groups"]
    assert [item["name"] for item in {group["type"]: group for group in ultra}["labels"]["items"]] == ["Ultra Records"]
    # Другой тип поиска лейблы не ищет.
    only_artists = client.get("/api/v1/search", params={"q": "trip", "type": "artist"}).json()["groups"]
    assert {group["type"]: group for group in only_artists}["labels"]["total"] == 0


def test_playback_session_from_label_without_tracks_is_not_found(tmp_path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.post("/api/v1/playback/sessions", json={"source_type": "label", "source_id": 999999})

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# склейка лейблов
# ---------------------------------------------------------------------------

def test_scan_puts_every_spelling_of_a_label_into_one_label(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    _, first = add_release(store, tmp_path, "R1", ("trip recordings",))
    _, cyrillic = add_release(store, tmp_path, "R2", ("ТРИП",))
    _, short = add_release(store, tmp_path, "R3", ("Trip",))
    _, legal = add_release(store, tmp_path, "R4", ("Universal Music Division Decca Records France",))
    add_release(store, tmp_path, "A1", ("Atlantic",))
    add_release(store, tmp_path, "A2", ("Atlantic Records",))
    _, pair = add_release(store, tmp_path, "R5", ("OWSLA/Atlantic",))
    _, unknown_pair = add_release(store, tmp_path, "R6", ("KR/LF",))

    assert release_labels(store, first) == release_labels(store, cyrillic) == release_labels(store, short) == [
        "trip recordings"
    ]
    assert release_labels(store, legal) == ["Decca Records France"]
    assert release_labels(store, pair) == ["Atlantic"]  # из пары — тот, у кого больше релизов
    assert release_labels(store, unknown_pair) == ["KR/LF"]  # частей нет среди лейблов — строка целиком
    labels, total = store.list_labels(limit=10, offset=0)
    assert [(label.name, label.release_count) for label in labels] == [
        ("Atlantic", 3), ("trip recordings", 3), ("Decca Records France", 1), ("KR/LF", 1),
    ]
    assert total == 4
    assert store.label_id_by_name("Трип") == store.label_id_by_name("trip recordings")


def make_legacy_label(store: Store, label_id: int, name: str) -> None:
    """Лейбл, заведённый до склейки: своё написание, ключей склейки нет."""
    with store.connect() as conn:
        conn.execute("UPDATE labels SET name = ?, normalized_name = ? WHERE id = ?", (name, name.casefold(), label_id))
        conn.execute("DELETE FROM label_aliases")


def test_first_start_merges_old_duplicates_into_the_found_label_under_its_official_name(tmp_path, monkeypatch):
    from app.models import LabelMetadata
    from app.store.label_merge import merge_labels_first_time

    store = init_api_store(tmp_path, monkeypatch)
    add_release(store, tmp_path, "T1", ("trip recordings",))
    add_release(store, tmp_path, "T2", ("trip recordings",))
    trip_id = store.label_id_by_name("trip recordings")
    store.save_label_metadata(LabelMetadata(
        name="trip recordings", description="Moscow label.", description_source="discogs",
        external_ids={"beatport": "44845"}, official_name="Trip Recordings",
    ))
    store.set_label_sync_state(trip_id, "found", keys_hash=None, beatport_id="44845")
    _, cyrillic_release = add_release(store, tmp_path, "C1", ("Tmp One",))
    cyrillic_id = store.label_id_by_name("Tmp One")
    store.set_label_liked(cyrillic_id, True)
    add_release(store, tmp_path, "S1", ("Tmp Two",))
    other_id = store.label_id_by_name("Tmp Two")
    # Другое написание, найденное синхронизацией как тот же лейбл Beatport.
    store.save_label_metadata(LabelMetadata(name="Tmp Two", external_ids={"beatport": "44845"}))
    add_release(store, tmp_path, "D1", ("Tmp Three",))
    decca_id = store.label_id_by_name("Tmp Three")
    store.set_label_sync_state(decca_id, "not_found", keys_hash="old")
    make_legacy_label(store, cyrillic_id, "ТРИП")
    make_legacy_label(store, other_id, "Nina's Trip Label")
    make_legacy_label(store, decca_id, "Universal Music Division Decca Records France")

    summary = merge_labels_first_time(store.db_path)

    assert (summary.merged, summary.renamed) == (2, 2)
    assert list(tmp_path.glob("app.db.pre-label-merge-*.bak"))
    trip = store.get_label(trip_id)
    assert (trip.name, trip.release_count, trip.liked, trip.description) == ("Trip Recordings", 4, True, "Moscow label.")
    assert store.get_label(cyrillic_id) is None and store.get_label(other_id) is None
    assert release_labels(store, cyrillic_release) == ["Trip Recordings"]
    # Ненайденный с новым названием ищется снова — под новым названием.
    assert store.get_label(decca_id).name == "Decca Records France"
    with store.connect() as conn:
        assert conn.execute("SELECT 1 FROM label_sync_state WHERE label_id = ?", (decca_id,)).fetchone() is None
    # Новые релизы с любым из прежних написаний идут в основной лейбл.
    _, later = add_release(store, tmp_path, "T3", ("ТРИП Recordings",))
    assert release_labels(store, later) == ["Trip Recordings"]
    # Ключи уже есть — второй запуск ничего не делает.
    assert merge_labels_first_time(store.db_path) is None


def test_label_pair_goes_to_the_part_with_more_releases_without_what_was_found_for_the_pair(tmp_path):
    from app.models import LabelMetadata

    store = Store(tmp_path / "app.db")
    store.init()
    add_release(store, tmp_path, "A1", ("A&M",))
    am_id = store.label_id_by_name("A&M")
    _, pair = add_release(store, tmp_path, "P1", ("Tmp Pair",))
    pair_id = store.label_id_by_name("Tmp Pair")
    # Beatport держит «A&M/Octone Records» отдельным лейблом — синхронизация нашла пару целиком.
    store.save_label_metadata(LabelMetadata(
        name="Tmp Pair", description="Joint venture.", description_source="beatport",
        external_ids={"beatport": "1"}, official_name="A&M/Octone Records",
    ))
    store.set_label_sync_state(pair_id, "found", keys_hash=None, beatport_id="1")
    _, kept = add_release(store, tmp_path, "K1", ("Tmp Kept",))
    kept_id = store.label_id_by_name("Tmp Kept")
    make_legacy_label(store, pair_id, "A&M/Octone Records")
    make_legacy_label(store, kept_id, "Ki/oon")

    summary = store.merge_duplicate_labels()

    assert summary.merged == 1
    am = store.get_label(am_id)
    assert release_labels(store, pair) == ["A&M"]
    # Найденное для пары к A&M не переходит, и основной — не пара, хоть она и «найдена».
    assert (am.name, am.release_count, am.description, am.external_ids) == ("A&M", 2, None, {})
    assert release_labels(store, kept) == ["Ki/oon"]  # частей нет среди лейблов — строка целиком
    _, later = add_release(store, tmp_path, "P2", ("A&M/Octone Records",))
    assert release_labels(store, later) == ["A&M"]
