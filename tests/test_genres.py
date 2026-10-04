"""Жанры релиза, лейбла и артиста (app/genres.py, app/store/genres.py)."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.genres import MAX_ENTITY_GENRES, entity_genres, release_genres
from app.library import TrackMetadataEnvelope
from app.main import app
from app.models import TrackPrediction
from app.scanner import ScannedTrack
from app.store import INITIALIZED_DB_PATHS, Store

E = "Electronic---"


def tracks(*scores: dict[str, float]) -> list[dict[str, float]]:
    return [{E + style: value for style, value in track.items()} for track in scores]


# ---------------------------------------------------------------------------
# методика релиза
# ---------------------------------------------------------------------------

def test_release_keeps_specific_styles_and_drops_known_misfires():
    genres = release_genres(tracks(
        {"Techno": 0.75, "Schranz": 0.40, "Hard Techno": 0.34, "Tech Trance": 0.30},
        {"Techno": 0.71, "Schranz": 0.40, "Hard Techno": 0.34, "Tech Trance": 0.16},
    ))

    # Tech Trance сильнее порога, но Discogs его на нашей библиотеке почти не подтверждает.
    assert [name for name, _score in genres] == ["Techno", "Schranz", "Hard Techno"]
    assert genres[0] == ("Techno", 0.73)


def test_release_shows_a_substyle_only_next_to_its_parent():
    with_parent = release_genres(tracks({"Drum n Bass": 0.6, "Halftime": 0.5, "Jungle": 0.2}))
    without_parent = release_genres(tracks({"Halftime": 0.5, "Dubstep": 0.3, "Drum n Bass": 0.05}))

    assert [name for name, _ in with_parent] == ["Drum n Bass", "Halftime", "Jungle"]
    # Drum n Bass 0.05 < 25% от лучшего — родителя среди стилей нет, Halftime не показываем.
    assert [name for name, _ in without_parent] == ["Dubstep"]


def test_release_threshold_is_relative_to_the_best_style_with_an_absolute_floor():
    strong = release_genres(tracks({"House": 0.8, "Deep House": 0.21, "Nu-Disco": 0.19}))
    weak = release_genres(tracks({"Downtempo": 0.16, "Ambient": 0.07, "Trip Hop": 0.045}))

    assert [name for name, _ in strong] == ["House", "Deep House"]  # 0.19 < 0.25 × 0.8
    assert [name for name, _ in weak] == ["Downtempo", "Ambient"]  # 0.045 ниже абсолютного минимума


def test_release_averages_over_analyzed_tracks_and_caps_at_five():
    genres = release_genres([
        {E + "House": 0.6, E + "Deep House": 0.5, E + "Techno": 0.4, E + "Nu-Disco": 0.35,
         E + "Tech House": 0.3, E + "Disco": 0.29},
        {},  # трек без предсказаний в среднее не входит
    ])

    assert [name for name, _ in genres] == ["House", "Deep House", "Techno", "Nu-Disco", "Tech House"]
    assert dict(genres)["House"] == 0.6


def test_release_counts_a_style_from_two_branches_once_per_track():
    genres = release_genres([{E + "Experimental": 0.3, "Rock---Experimental": 0.2, E + "Ambient": 0.25}])

    assert dict(genres) == {"Experimental": 0.3, "Ambient": 0.25}


def test_release_without_predictions_has_no_genres():
    assert release_genres([]) == []
    assert release_genres([{}, {}]) == []


# ---------------------------------------------------------------------------
# методика лейбла / артиста
# ---------------------------------------------------------------------------

def test_entity_counts_releases_per_style_and_skips_rare_ones():
    lists = [["Drum n Bass", "Halftime"]] * 30 + [["Drum n Bass", "Jungle"]] * 10 + [["Drum n Bass", "Juke"]]
    lists += [[]] * 50  # не проанализированы — в долю не входят

    genres = entity_genres(lists)

    # Juke — у 1 из 41 релиза, меньше 5%.
    assert genres == [("Drum n Bass", 41), ("Halftime", 30), ("Jungle", 10)]


def test_entity_shows_at_most_ten_styles():
    lists = [[f"Style {i:02d}"] for i in range(15)]

    assert len(entity_genres(lists)) == MAX_ENTITY_GENRES


# ---------------------------------------------------------------------------
# кэш в базе
# ---------------------------------------------------------------------------

def add_track(store: Store, tmp_path: Path, album: str, number: int, genres: dict[str, float], *,
              artist: str = "Artist", labels: tuple[str, ...] = ("Label",)) -> tuple[int, int]:
    path = tmp_path / "music" / album / f"{number:02d}.flac"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake")
    scanned = ScannedTrack(path=path, artist=artist, title=f"{album} {number}", album=album, year=2020,
                           duration=180.0, file_size=4, mtime=1)
    track_id, _changed = store.upsert_track(scanned)
    release_id = store.upsert_normalized_track_sidecars(track_id, TrackMetadataEnvelope(
        title=scanned.title, artist=artist, album=album, year=2020, duration=180.0, path=str(path),
        track_number=number, record_labels=labels,
    ))
    set_genres(store, track_id, genres)
    return track_id, release_id


def set_genres(store: Store, track_id: int, genres: dict[str, float]) -> None:
    ranked = sorted(genres.items(), key=lambda item: -item[1])
    store.save_predictions(track_id, "genre_discogs400", [
        TrackPrediction(label=E + style, score=score, rank=rank) for rank, (style, score) in enumerate(ranked, 1)
    ])


def cached_signature(store: Store, release_id: int) -> str | None:
    with store.connect() as conn:
        row = conn.execute("SELECT signature FROM release_genres WHERE release_id = ?", (release_id,)).fetchone()
    return row[0] if row else None


def test_release_genres_are_cached_and_recomputed_when_tracks_change(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    _t1, release_id = add_track(store, tmp_path, "EP", 1, {"Techno": 0.8, "Hard Techno": 0.4})
    t2, _ = add_track(store, tmp_path, "EP", 2, {})
    with store.connect() as conn:
        conn.execute("DELETE FROM track_predictions WHERE track_id = ?", (t2,))

    assert store.release_genres([release_id]) == {release_id: [("Techno", 0.8), ("Hard Techno", 0.4)]}
    assert cached_signature(store, release_id).startswith("1:")

    # Второй трек проанализирован — у релиза теперь два трека в среднем.
    set_genres(store, t2, {"House": 0.8})
    # равные оценки — по алфавиту
    assert store.release_genres([release_id])[release_id] == [("House", 0.4), ("Techno", 0.4), ("Hard Techno", 0.2)]
    assert cached_signature(store, release_id).startswith("2:")

    # Файл пропал — его жанры больше не считаются.
    with store.connect() as conn:
        conn.execute("UPDATE tracks SET missing_at = '2026-01-01T00:00:00Z' WHERE id = ?", (t2,))
    assert store.release_genres([release_id])[release_id] == [("Techno", 0.8), ("Hard Techno", 0.4)]


def test_refresh_fills_the_cache_used_by_label_cards(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    add_track(store, tmp_path, "A", 1, {"Drum n Bass": 0.7, "Halftime": 0.6}, labels=("Shogun",))
    add_track(store, tmp_path, "B", 1, {"Drum n Bass": 0.8, "Jungle": 0.4}, labels=("Shogun",))
    label_id = store.label_id_by_name("Shogun")

    # Карточки читают только кэш: до пересчёта стилей у лейбла нет.
    assert store.top_label_genres([label_id]) == {}
    assert store.refresh_release_genres() == 2
    assert store.refresh_release_genres() == 0  # всё свежее — пересчитывать нечего
    assert store.top_label_genres([label_id]) == {label_id: ["Drum n Bass", "Halftime"]}


# ---------------------------------------------------------------------------
# API
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


def test_release_label_and_artist_pages_show_genres(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    _t, coyu = add_track(store, tmp_path, "Revisionist", 1, {"Techno": 0.7, "Schranz": 0.4, "Tech Trance": 0.3},
                         artist="Coyu", labels=("Suara",))
    add_track(store, tmp_path, "Revisionist", 2, {"Techno": 0.75, "Hard Techno": 0.45}, artist="Coyu", labels=("Suara",))
    add_track(store, tmp_path, "Groove", 1, {"Tech House": 0.6, "House": 0.4}, artist="Other", labels=("Suara",))
    label_id = store.label_id_by_name("Suara")
    artist_id = store.artist_ids_by_names(["Coyu"])["coyu"]
    client = TestClient(app)

    release = client.get(f"/api/v1/releases/{coyu}").json()["release"]
    assert release["genres"] == ["Techno", "Hard Techno", "Schranz"]

    label = client.get(f"/api/v1/labels/{label_id}").json()["label"]
    assert label["genres"] == [
        {"name": "Hard Techno", "release_count": 1}, {"name": "House", "release_count": 1},
        {"name": "Schranz", "release_count": 1}, {"name": "Tech House", "release_count": 1},
        {"name": "Techno", "release_count": 1},
    ]

    artist = client.get(f"/api/v1/artists/{artist_id}").json()
    assert [genre["name"] for genre in artist["genres"]] == ["Hard Techno", "Schranz", "Techno"]

    # Список лейблов и полка дашборда: два главных стиля — из кэша, заполненного страницами выше.
    listed = client.get("/api/v1/labels").json()["items"][0]
    assert listed["top_genres"] == ["Hard Techno", "House"]
    shelf = client.get("/api/v1/dashboard/shelves/labels").json()["items"][0]
    assert shelf["top_genres"] == ["Hard Techno", "House"]


def test_release_without_analysis_has_empty_genres(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    track_id, release_id = add_track(store, tmp_path, "Raw", 1, {})
    with store.connect() as conn:
        conn.execute("DELETE FROM track_predictions WHERE track_id = ?", (track_id,))

    release = TestClient(app).get(f"/api/v1/releases/{release_id}").json()["release"]

    assert release["genres"] == []
