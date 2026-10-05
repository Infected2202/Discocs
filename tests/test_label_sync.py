"""Синхронизация лейблов из админки (app/services/label_sync, app/api/label_sync.py)."""
from __future__ import annotations

import io
import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.integration_secrets import (
    IntegrationSecretError,
    decrypt_integration_secret,
    encrypt_integration_secret,
)
from app.library import TrackMetadataEnvelope
from app.main import app
from app.scanner import ScannedTrack
from app.services.jobs import JobStatus, has_active_job
from app.services.label_sync.clients import BeatportClient, ServiceAuthError
from app.services.label_sync.http_cache import HttpCache
from app.services.label_sync.job import LabelKeys, LabelSyncSummary, labels_to_sync, label_keys, run_label_sync
from app.services.label_sync.resolver import (
    LabelResolver,
    LabelResult,
    is_placeholder_label,
    label_key,
    titles_overlap,
)
from app.services.label_sync.tags import _tag_values
from app.state import JOBS, JOBS_LOCK
from app.store import INITIALIZED_DB_PATHS, Store
from app.store.label_sync import LabelSyncCandidate

# ---------------------------------------------------------------------------
# шифрование доступов
# ---------------------------------------------------------------------------


def test_credentials_are_encrypted_with_the_server_key():
    blob = encrypt_integration_secret("server-key", {"token": "abc123"})

    assert "abc123" not in blob
    assert decrypt_integration_secret("server-key", blob) == {"token": "abc123"}
    with pytest.raises(IntegrationSecretError, match="do not match"):
        decrypt_integration_secret("rotated-key", blob)
    with pytest.raises(IntegrationSecretError, match="DISCOCS_SERVICE_TOKEN"):
        encrypt_integration_secret("", {"token": "abc123"})


# ---------------------------------------------------------------------------
# кого искать
# ---------------------------------------------------------------------------

def candidate(label_id: int, status: str | None, keys_hash: str | None = None) -> LabelSyncCandidate:
    return LabelSyncCandidate(label_id=label_id, name=f"L{label_id}", status=status, keys_hash=keys_hash)


def test_only_new_errored_and_not_found_with_new_releases_are_looked_up():
    keys = {i: LabelKeys(barcodes=(f"{i}000000001",), isrcs=()) for i in range(1, 7)}
    old = LabelKeys(barcodes=("5000000001", "999"), isrcs=())
    baseline: list[tuple[int, str]] = []
    candidates = [
        candidate(1, None),                                    # новый
        candidate(2, "found", keys[2].hash),                   # найден — не трогаем
        candidate(3, "error", keys[3].hash),                   # ошибка — повторить
        candidate(4, "not_found", keys[4].hash),               # ничего не докачали
        candidate(5, "not_found", old.hash),                   # набор штрихкодов изменился
        candidate(6, "not_found", None),                       # отмечен старым скриптом
    ]

    todo = labels_to_sync(candidates, lambda ids: {i: keys[i] for i in ids}, retry_not_found=False,
                          only_label_id=None, remember_baseline=lambda i, h: baseline.append((i, h)))

    assert [c.label_id for c, _keys in todo] == [1, 3, 5]
    # У отмеченного скриптом лейбла только запоминаем ключи — искать, когда появятся новые.
    assert baseline == [(6, keys[6].hash)]


def test_retry_not_found_and_single_label_override_the_state():
    keys = {i: LabelKeys(barcodes=(), isrcs=()) for i in (1, 2)}
    candidates = [candidate(1, "not_found", keys[1].hash), candidate(2, "found", keys[2].hash)]

    retry = labels_to_sync(candidates, lambda ids: {i: keys[i] for i in ids}, retry_not_found=True,
                           only_label_id=None, remember_baseline=lambda i, h: None)
    single = labels_to_sync(candidates, lambda ids: {i: keys[i] for i in ids}, retry_not_found=False,
                            only_label_id=2, remember_baseline=lambda i, h: None)

    assert [c.label_id for c, _ in retry] == [1]
    assert [c.label_id for c, _ in single] == [2]


# ---------------------------------------------------------------------------
# штрихкоды и ISRC
# ---------------------------------------------------------------------------

def add_track(store: Store, tmp_path: Path, album: str, number: int, *, label: str, isrc: str | None = None) -> int:
    path = tmp_path / "music" / album / f"{number:02d}.flac"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake")
    scanned = ScannedTrack(path=path, artist="Artist", title=f"{album} {number}", album=album, year=2020,
                           duration=180.0, file_size=4, mtime=1)
    track_id, _ = store.upsert_track(scanned)
    store.upsert_normalized_track_sidecars(track_id, TrackMetadataEnvelope(
        title=scanned.title, artist="Artist", album=album, year=2020, duration=180.0, path=str(path),
        track_number=number, record_labels=(label,),
    ))
    raw = {"path": str(path), "isrc": [isrc] if isrc else []}
    store.upsert_external_track("navidrome", f"nd-{track_id}", track_id, json.dumps(raw))
    return track_id


def test_barcodes_come_from_tags_small_releases_first_and_are_cached_until_the_file_changes(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    for n in (1, 2, 3):
        add_track(store, tmp_path, "Album", n, label="Trip", isrc=f"GBAAA00000{n}")
    add_track(store, tmp_path, "Single", 1, label="Trip", isrc="GBBBB0000001")
    label_id = store.label_id_by_name("Trip")
    reads: list[str] = []

    def read(path: Path) -> str | None:
        reads.append(path.parent.name)
        return {"Album": "0111111111111", "Single": "0222222222222"}[path.parent.name]

    keys = label_keys(store, [label_id], read)[label_id]

    assert keys.barcodes == ("0222222222222", "0111111111111")  # сингл первым
    assert keys.isrcs == ("GBBBB0000001", "GBAAA000001")  # по одному ISRC на релиз
    assert len(reads) == 4

    reads.clear()
    label_keys(store, [label_id], read)
    assert reads == []  # файлы не менялись — теги не перечитываем

    (tmp_path / "music" / "Single" / "01.flac").write_bytes(b"retagged")
    label_keys(store, [label_id], read)
    assert reads == ["Single"]


def test_tag_values_cover_vorbis_id3_and_mp4_freeform():
    class Frame:
        def __init__(self, desc, text):
            self.desc, self.text = desc, text

    class Id3:
        def getall(self, key):
            return [Frame("BARCODE", ["0123456789012"]), Frame("OTHER", ["x"])] if key == "TXXX" else []

    assert _tag_values(Id3()) == ["0123456789012"]
    assert _tag_values({"barcode": ["0123456789012"]}) == ["0123456789012"]
    assert _tag_values({"----:com.apple.iTunes:UPC": [b"0123456789012"]}) == ["0123456789012"]


# ---------------------------------------------------------------------------
# поиск лейбла
# ---------------------------------------------------------------------------

class FakeApi:
    def __init__(self, responses: dict[str, dict]):
        self.responses = responses
        self.calls: list[str] = []

    def get(self, path: str, **params) -> dict:
        key = path + ("?" + "&".join(f"{k}={v}" for k, v in sorted(params.items())) if params else "")
        self.calls.append(key)
        return self.responses.get(key, self.responses.get(path, {}))

    def label(self, label_id: int) -> dict:
        return self.get(f"/catalog/labels/{label_id}/")

    def search(self, query: str, type_: str, limit: int = 10) -> list[dict]:
        return self.get("/catalog/search/", q=query, type=type_, per_page=limit).get(type_, [])


class FakeWeb(FakeApi):
    def get_json(self, url: str, **params) -> dict:
        return self.get(url, **params)

    def download(self, url: str) -> bytes | None:
        return self.responses.get(f"download:{url}")


def png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_label_is_found_through_isrc_when_barcodes_miss_and_wikipedia_wins_over_discogs():
    beatport = FakeApi({
        "/catalog/releases/?upc=0123456789012": {"results": []},
        "/catalog/tracks/?isrc=ES84B1900207": {"results": [{"release": {"label": {"id": 11133, "name": "Suara"}}}]},
        "/catalog/labels/11133/": {"slug": "suara", "image": {"dynamic_uri": "https://img/{w}x{h}/logo.jpg"}},
    })
    discogs = FakeApi({
        "/database/search?barcode=0123456789012&per_page=5&type=release": {"results": [{"id": 9}]},
        "/releases/9": {"labels": [{"id": 77, "name": "Suara (2)"}, {"id": 5, "name": "Distro"}]},
        "/labels/77": {"profile": "Run by [a=Coyu].", "urls": ["https://suara.com", "javascript:x"]},
    })
    web = FakeWeb({
        "https://www.wikidata.org/w/api.php?action=query&format=json&list=search&srsearch=haswbstatement:P1955=77":
            {"query": {"search": [{"title": "Q1"}]}},
        "https://www.wikidata.org/w/api.php?action=wbgetentities&format=json&ids=Q1&props=sitelinks&sitefilter=ruwiki|enwiki":
            {"entities": {"Q1": {"sitelinks": {"enwiki": {"title": "Suara (label)"}}}}},
        "https://en.wikipedia.org/w/api.php?action=query&exintro=1&explaintext=1&format=json&prop=extracts&redirects=1&titles=Suara (label)":
            {"query": {"pages": {"1": {"extract": "Suara is a Barcelona label."}}}},
    })
    resolver = LabelResolver(beatport, discogs, web)

    result = resolver.resolve("Suara", ["0123456789012"], ["ES84B1900207"], lambda: [])

    assert result.external_ids == {"beatport": "11133", "discogs": "77", "wikidata": "Q1"}
    assert result.how == {"beatport": "isrc", "discogs": "barcode"}
    assert (result.image_url, result.image_source) == ("https://img/500x500/logo.jpg", "beatport")
    assert (result.description, result.description_source) == ("Suara is a Barcelona label.", "wikipedia_en")
    assert [link["url"] for link in result.links] == [
        "https://suara.com", "https://www.discogs.com/label/77", "https://www.beatport.com/label/suara/11133",
    ]


def test_name_search_needs_a_release_from_the_library_and_placeholder_is_not_an_image():
    beatport = FakeApi({
        "/catalog/search/?per_page=10&q=Trip&type=labels": {"labels": [{"id": 1, "name": "Trip"}]},
        "/catalog/labels/1/releases/?page=1&per_page=100": {"results": [{"name": "Somebody Else's Record"}]},
    })
    discogs = FakeApi({
        "/database/search?per_page=10&q=Trip&type=label": {"results": [{"id": 3, "title": "Trip (3)"}]},
        "/labels/3/releases?page=1&per_page=100": {"releases": [{"title": "Amiga EP"}], "pagination": {"pages": 1}},
        "/labels/3": {"profile": "Moscow label of [a=Nina Kraviz].",
                      "images": [{"type": "secondary", "uri": "https://d/2.jpg"}, {"type": "primary", "uri": "https://d/1.jpg"}]},
    })
    resolver = LabelResolver(beatport, discogs, FakeWeb({}))

    result = resolver.resolve("Trip", [], [], lambda: ["Amiga"])

    assert result.external_ids == {"discogs": "3"}  # чужой Trip на Beatport не принят
    assert (result.image_url, result.image_source) == ("https://d/1.jpg", "discogs")
    assert (result.description, result.description_source) == ("Moscow label of [a=Nina Kraviz].", "discogs")


def test_label_names_compare_without_disambiguation_and_suffix_and_bootleg_placeholders_are_recognised():
    assert label_key("Warner Bros. Records (2)") == label_key("Warner Bros.") == "warner bros"
    assert label_key("Mute Records Ltd.") == label_key("Mute")
    assert label_key("Harthouse Digital") != label_key("Harthouse")
    assert label_key("Records") == "records"  # от названия из одного хвоста что-то остаётся
    assert label_key("Ultra Records, LLC") == label_key("Ultra") == "ultra"
    assert label_key("Crammed Discs") == label_key("Crammed")
    assert label_key('ООО "Союз Мьюзик"') == label_key("Союз Мьюзик") == "союз мьюзик"
    assert label_key("Samurai Music") != label_key("Samurai Red Seal")
    assert is_placeholder_label({"profile": "This is [b][u]NOT[/u][/b] a real label. Use this for all unofficial releases"})
    assert is_placeholder_label({"profile": "This page catalogues counterfeit editions of releases originally on [l=Elektra]"})
    assert is_placeholder_label({"profile": "For the copyright or licensing entries of label [l1490].\nFor the label please use [l1490]."})
    assert not is_placeholder_label({"profile": "Texas Drum & Bass label originally established in 2000."})
    assert not is_placeholder_label({"profile": "Industrial label. Beware of counterfeit copies of early releases."})


def test_release_label_with_another_name_or_a_bootleg_placeholder_is_not_accepted():
    beatport = FakeApi({})
    discogs = FakeApi({
        # Релиз Warm Communications числится только за дистрибьютором — это не наш лейбл.
        "/database/search?barcode=111&per_page=5&type=release": {"results": [{"id": 1}]},
        "/releases/1": {"labels": [{"id": 276637, "name": "PRSPCT Recordings"}]},
        # Совпало по названию, но это служебная запись для бутлегов — тоже нет.
        "/database/search?barcode=222&per_page=5&type=release": {"results": [{"id": 2}]},
        "/releases/2": {"labels": [{"id": 132296, "name": "Warm Communications (2)"}]},
        "/labels/132296": {"name": "Warm Communications (2)", "profile": "This is [b]NOT[/b] a real label."},
        "/database/search?per_page=10&q=Warm Communications&type=label": {
            "results": [{"id": 132296, "title": "Warm Communications (2)"}, {"id": 9672, "title": "Warm Communications"}],
        },
        "/labels/9672": {"name": "Warm Communications", "profile": "Texas Drum & Bass label."},
        "/labels/9672/releases?page=1&per_page=100": {"releases": [{"title": "Shades Of Me & You"}], "pagination": {"pages": 1}},
    })
    resolver = LabelResolver(beatport, discogs, FakeWeb({}))

    assert resolver.discogs_by_barcodes("Warm Communications", ["111", "222"]) is None
    assert resolver.discogs_by_name("Warm Communications", ["Shades Of Me & You"]) == 9672
    assert "/labels/132296/releases?page=1&per_page=100" not in discogs.calls  # заглушку даже не проверяли по релизам


def test_big_discogs_catalogue_is_confirmed_by_searching_our_release_inside_the_label():
    pages = {"releases": [{"title": f"Other {n}"} for n in range(100)], "pagination": {"pages": 40}}
    discogs = FakeApi({
        "/database/search?per_page=10&q=Cooking Vinyl&type=label": {
            "results": [{"id": 4184, "title": "Cooking Vinyl"}, {"id": 5000, "title": "Cooking Vinyl (2)"}],
        },
        "/labels/4184": {"name": "Cooking Vinyl", "profile": "London based independent label."},
        "/labels/5000": {"name": "Cooking Vinyl (2)", "profile": "Home-made cassettes."},
        "/labels/4184/releases?page=1&per_page=100": pages,
        "/labels/4184/releases?page=2&per_page=100": pages,
        "/labels/4184/releases?page=3&per_page=100": pages,
        "/database/search?label=Cooking Vinyl&per_page=5&release_title=No Sounds Are Out of Bounds&type=release": {
            "results": [{"id": 1, "title": "The Orb - No Sounds Are Out Of Bounds"}],
        },
        "/releases/1": {"labels": [{"id": 4184, "name": "Cooking Vinyl"}]},
    })
    resolver = LabelResolver(FakeApi({}), discogs, FakeWeb({}))

    assert resolver.discogs_by_name("Cooking Vinyl", ["No Sounds Are Out of Bounds (Deluxe)"]) == 4184
    assert "/labels/4184/releases?page=4&per_page=100" not in discogs.calls  # каталог дальше не листаем

    # Поиск по названию лейбла отдаёт и релизы однофамильца: релиз у «Cooking Vinyl (2)» не
    # подтверждает 4184, зато подтверждает 5000.
    discogs.responses["/releases/1"] = {"labels": [{"id": 5000, "name": "Cooking Vinyl (2)"}]}
    discogs.responses["/labels/5000/releases?page=1&per_page=100"] = {"releases": [], "pagination": {"pages": 1}}
    assert resolver.discogs_by_name("Cooking Vinyl", ["No Sounds Are Out of Bounds"]) == 5000


def test_big_beatport_catalogue_is_confirmed_by_searching_our_release_inside_the_label():
    beatport = FakeApi({
        "/catalog/search/?per_page=10&q=Ultra Records, LLC&type=labels": {"labels": [{"id": 907, "name": "Ultra"}]},
        "/catalog/labels/907/releases/?page=1&per_page=100": {"results": [{"name": "Something Else"}], "next": None},
        # Поиск Beatport неточный: релиз с другим названием не подтверждает лейбл.
        "/catalog/releases/?label_id=907&name=Matrix&per_page=5": {"results": [{"name": "Need To Feel Loved - Matrix Remix"}]},
        "/catalog/releases/?label_id=907&name=Vodka & Orange EP&per_page=5": {"results": [{"name": "Vodka & Orange EP"}]},
    })
    resolver = LabelResolver(beatport, FakeApi({}), FakeWeb({}))

    assert resolver.beatport_by_name("Ultra Records, LLC", ["Matrix"]) is None
    assert resolver.beatport_by_name("Ultra Records, LLC", ["Matrix", "Vodka & Orange EP"]) == 907


def test_isrc_finds_the_own_label_among_compilations_of_other_labels():
    beatport = FakeApi({
        "/catalog/tracks/?isrc=GB0000000001": {"results": [
            {"release": {"label": {"id": 5, "name": "Hospital Records Compilations"}}},
            {"release": {"label": {"id": 7, "name": "Warm Communications"}}},
        ]},
        "/catalog/tracks/?isrc=GB0000000002": {"results": [{"release": {"label": {"id": 5, "name": "Some Compilation"}}}]},
    })
    resolver = LabelResolver(beatport, FakeApi({}), FakeWeb({}))

    assert resolver.beatport_by_isrcs("Warm Communications", ["GB0000000001"]) == 7
    # Раньше два чужих голоса подтверждали чужой лейбл — теперь чужое название не принимается вовсе.
    assert resolver.beatport_by_isrcs("Warm Communications", ["GB0000000002", "GB0000000002"]) is None


def test_saved_match_is_wrong_for_a_placeholder_or_another_name():
    discogs = FakeApi({
        "/labels/132296": {"name": "Warner Bros. Records (2)", "profile": "This is NOT a real label."},
        "/labels/276637": {"name": "PRSPCT Recordings", "profile": "Rotterdam based record label."},
        "/labels/9672": {"name": "Warm Communications", "profile": "Texas Drum & Bass label."},
    })
    beatport = FakeApi({"/catalog/labels/8559/": {"name": "Warm Communications"}})
    resolver = LabelResolver(beatport, discogs, FakeWeb({}))

    assert resolver.mismatch("Warner Bros. Records", None, "132296") == "discogs"
    assert resolver.mismatch("Warm Communications", "8559", "276637") == "discogs"
    assert resolver.mismatch("Warm Communications", "8559", "9672") is None


def test_release_titles_compare_without_brackets_and_ep_suffix():
    assert titles_overlap(["Amiga"], ["Amiga EP"])
    assert titles_overlap(["Hot Steel: Round 2"], ["Hot Steel - Round 2 (Remastered)"])
    assert not titles_overlap(["Amiga"], ["Amigos"])


# ---------------------------------------------------------------------------
# прогон целиком
# ---------------------------------------------------------------------------

class ScriptedResolver:
    def __init__(self, results: dict[str, LabelResult | Exception]):
        self.results = results
        self.web = FakeWeb({"download:https://img/logo.png": png()})
        self.calls: list[str] = []
        self.wrong: dict[str, str] = {}
        self.checked: list[tuple[str, object, object]] = []
        self.names: dict[str, str] = {}

    def official_name(self, beatport_id, discogs_id):
        return self.names.get(str(beatport_id or discogs_id))

    def mismatch(self, name, beatport_id, discogs_id):
        self.checked.append((name, beatport_id, discogs_id))
        return self.wrong.get(name)

    def resolve(self, name, barcodes, isrcs, release_titles):
        self.calls.append(name)
        outcome = self.results[name]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def init_api_store(tmp_path: Path, monkeypatch) -> Store:
    db_path = tmp_path / "app.db"
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    monkeypatch.setenv("DISCOCS_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("DISCOCS_SERVICE_TOKEN", "svc-secret")
    monkeypatch.delenv("DISCOCS_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("DISCOCS_NAVIDROME_URL", raising=False)
    store = Store(db_path)
    store.init()
    return store


def label_state(store: Store, name: str) -> tuple[str, str | None] | None:
    with store.connect() as conn:
        row = conn.execute(
            "SELECT s.status, s.discogs_id FROM label_sync_state s JOIN labels l ON l.id = s.label_id WHERE l.name = ?",
            (name,),
        ).fetchone()
    return (row[0], row[1]) if row else None


def test_run_saves_found_labels_remembers_misses_and_skips_them_next_time(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    add_track(store, tmp_path, "A", 1, label="Found")
    add_track(store, tmp_path, "B", 1, label="Missing")
    add_track(store, tmp_path, "C", 1, label="Broken")
    from app.config import Settings
    settings = Settings.from_env()
    resolver = ScriptedResolver({
        "Found": LabelResult(image_url="https://img/logo.png", image_source="beatport",
                             description="Text", description_source="beatport",
                             links=[{"url": "https://found.example"}], external_ids={"discogs": "42"}),
        "Missing": LabelResult(),
        "Broken": RuntimeError("Discogs went away"),
    })
    seen: list[tuple[int, int]] = []

    summary = run_label_sync(store, settings, resolver, progress=lambda d, t, c: seen.append((d, t)),
                             workers=1, read=lambda path: None)

    assert (summary.checked, summary.found, summary.not_found, summary.errors, summary.images) == (3, 1, 1, 1, 1)
    assert seen[0] == (0, 3) and (3, 3) in seen
    found = store.get_label(store.label_id_by_name("Found"))
    assert found.description == "Text" and found.external_ids == {"discogs": "42"}
    assert Path(found.image_path).read_bytes() == png()
    assert label_state(store, "Found") == ("found", "42")
    assert label_state(store, "Missing") == ("not_found", None)
    assert label_state(store, "Broken")[0] == "error"

    # Второй прогон: найденный и ненайденный без новых релизов пропускаются, ошибка — повторяется.
    resolver.calls.clear()
    resolver.results["Broken"] = LabelResult()
    run_label_sync(store, settings, resolver, workers=1, read=lambda path: None)
    assert resolver.calls == ["Broken"]

    # Докачали релиз «Missing» с ISRC — набор ключей изменился, лейбл ищется снова.
    resolver.calls.clear()
    add_track(store, tmp_path, "B2", 1, label="Missing", isrc="GBNEW0000001")
    run_label_sync(store, settings, resolver, workers=1, read=lambda path: None)
    assert resolver.calls == ["Missing"]


def test_recheck_looks_up_again_only_wrong_matches_and_drops_their_old_image(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    add_track(store, tmp_path, "A", 1, label="Good")
    add_track(store, tmp_path, "B", 1, label="Warm")
    from app.config import Settings
    settings = Settings.from_env()
    resolver = ScriptedResolver({
        "Good": LabelResult(image_url="https://img/logo.png", image_source="beatport",
                            external_ids={"beatport": "1"}),
        "Warm": LabelResult(image_url="https://img/logo.png", image_source="discogs",
                            description="Rotterdam based label.", description_source="discogs",
                            links=[{"url": "https://prspct.nl"}], external_ids={"discogs": "276637"}),
    })
    run_label_sync(store, settings, resolver, workers=1, read=lambda path: None)
    warm_id = store.label_id_by_name("Warm")

    resolver.calls.clear()
    resolver.wrong = {"Warm": "discogs"}
    resolver.results["Warm"] = LabelResult(links=[{"url": "https://warmcommunications.bandcamp.com/"}],
                                           external_ids={"discogs": "9672"})
    summary = run_label_sync(store, settings, resolver, recheck_found=True, workers=1, read=lambda path: None)

    assert sorted(name for name, *_ in resolver.checked) == ["Good", "Warm"]
    assert ("Warm", None, "276637") in resolver.checked
    assert resolver.calls == ["Warm"]  # верную привязку заново не ищем
    assert (summary.checked, summary.mismatched, summary.found) == (2, 1, 1)
    assert summary.message().endswith("wrong matches looked up again 1")
    warm = store.get_label(warm_id)
    assert warm.external_ids == {"discogs": "9672"}
    assert [link["url"] for link in warm.links] == ["https://warmcommunications.bandcamp.com/"]
    assert warm.image_path is None and warm.description is None  # картинка и текст PRSPCT не остались
    assert label_state(store, "Warm") == ("found", "9672")
    assert store.get_label(store.label_id_by_name("Good")).image_path is not None


def test_recheck_keeps_a_hand_written_description_and_forgets_a_match_that_is_not_found_again(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    add_track(store, tmp_path, "A", 1, label="Warner")
    from app.config import Settings
    settings = Settings.from_env()
    resolver = ScriptedResolver({"Warner": LabelResult(
        image_url="https://img/logo.png", image_source="discogs", links=[{"url": "https://bootleg.example"}],
        external_ids={"discogs": "132296"},
    )})
    run_label_sync(store, settings, resolver, workers=1, read=lambda path: None)
    label_id = store.label_id_by_name("Warner")
    store.set_label_description(label_id, "Написано вручную.")

    resolver.wrong = {"Warner": "discogs"}
    resolver.results["Warner"] = LabelResult()
    run_label_sync(store, settings, resolver, recheck_found=True, workers=1, read=lambda path: None)

    label = store.get_label(label_id)
    assert (label.description, label.description_source) == ("Написано вручную.", "editorial")
    assert label.links == [] and label.external_ids == {} and label.image_path is None
    assert label_state(store, "Warner") == ("not_found", None)


def test_expired_beatport_login_stops_the_whole_run(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    add_track(store, tmp_path, "A", 1, label="One")
    from app.config import Settings

    with pytest.raises(ServiceAuthError):
        run_label_sync(store, Settings.from_env(), ScriptedResolver({"One": ServiceAuthError("expired")}),
                       workers=1, read=lambda path: None)
    # Вход истёк — это не «лейбл не найден»: состояние не записываем, лейбл пойдёт в следующий раз.
    assert label_state(store, "One") is None


def test_label_sync_job_does_not_hold_the_shared_queue():
    job = JobStatus(id="label-sync-test", kind="label-sync", status="running", message="")
    with JOBS_LOCK:
        saved = dict(JOBS)
        JOBS.clear()
        JOBS[job.id] = job
    try:
        assert has_active_job(store=_NoDurableJobs()) is False
        with JOBS_LOCK:
            JOBS["sync"] = JobStatus(id="sync", kind="navidrome-sync", status="running", message="")
        assert has_active_job(store=_NoDurableJobs()) is True
    finally:
        with JOBS_LOCK:
            JOBS.clear()
            JOBS.update(saved)


class _NoDurableJobs:
    def has_active_analysis_job(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# Beatport: вход и продление
# ---------------------------------------------------------------------------

def test_beatport_token_renews_itself_and_hands_back_the_new_refresh_token(tmp_path):
    saved: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/o/token/"):
            assert b"refresh_token=old-refresh" in request.content
            return httpx.Response(200, json={"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer new-access"
        return httpx.Response(200, json={"results": [{"id": 1}]})

    client = BeatportClient(
        {"access_token": "old", "refresh_token": "old-refresh", "expires_at": time.time() - 1},
        saved.append, HttpCache(tmp_path / "cache.db"),
        httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None,
    )

    assert client.get("/catalog/releases/", upc="1") == {"results": [{"id": 1}]}
    assert saved[0]["refresh_token"] == "new-refresh"
    # Ответ закэширован: второй раз Beatport не спрашиваем.
    assert client.get("/catalog/releases/", upc="1") == {"results": [{"id": 1}]}


def beatport_login_transport(password_ok: bool = True) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/auth/login/"):
            return httpx.Response(200 if password_ok else 401, json={})
        if path.endswith("/auth/o/authorize/"):
            return httpx.Response(302, headers={"Location": "https://api.beatport.com/v4/auth/o/post-message/?code=abc"})
        if path.endswith("/auth/o/token/"):
            return httpx.Response(200, json={"access_token": "acc", "refresh_token": "ref", "expires_in": 36000})
        if path == "/database/search":
            ok = request.headers.get("Authorization") == f"Discogs key={DISCOGS_KEY}, secret={DISCOGS_SECRET_VALUE}"
            return httpx.Response(200 if ok else 401, json={"results": []})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


DISCOGS_KEY = "abcdefghijKLMN"
DISCOGS_SECRET_VALUE = "secretSECRET1234"
DISCOGS_APP = {"key": DISCOGS_KEY, "secret": DISCOGS_SECRET_VALUE}


def test_admin_connects_services_without_storing_the_password(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    monkeypatch.setattr("app.api.label_sync.new_http_client",
                        lambda: httpx.Client(transport=beatport_login_transport()))
    client = TestClient(app)

    status = client.get("/api/v1/label-sync").json()
    assert status["beatport"] == {"connected": False} and status["discogs"] == {"connected": False}
    assert client.post("/api/v1/jobs/label-sync", json={}).status_code == 409

    connected = client.post("/api/v1/label-sync/beatport", json={"username": "me", "password": "hunter2"})
    assert connected.status_code == 200
    assert connected.json()["beatport"]["username"] == "me"
    assert client.put("/api/v1/label-sync/discogs", json=DISCOGS_APP).json()["discogs"] == {
        "connected": True, "key_hint": "KLMN",
    }
    assert decrypt_integration_secret("svc-secret", store.get_integration_secret("discogs")) == DISCOGS_APP

    blob = store.get_integration_secret("beatport")
    assert "hunter2" not in blob
    assert "password" not in decrypt_integration_secret("svc-secret", blob)
    assert client.delete("/api/v1/label-sync/discogs").json()["discogs"] == {"connected": False}


def test_wrong_beatport_password_is_reported(tmp_path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    monkeypatch.setattr("app.api.label_sync.new_http_client",
                        lambda: httpx.Client(transport=beatport_login_transport(password_ok=False)))

    response = TestClient(app).post("/api/v1/label-sync/beatport", json={"username": "me", "password": "bad"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "beatport_login_failed"


def test_rejected_discogs_key_is_reported_and_not_saved(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    monkeypatch.setattr("app.api.label_sync.new_http_client",
                        lambda: httpx.Client(transport=beatport_login_transport()))

    response = TestClient(app).put("/api/v1/label-sync/discogs",
                                   json={"key": DISCOGS_KEY, "secret": "wrongSECRET12345"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "discogs_rejected"
    assert store.get_integration_secret("discogs") is None


def test_discogs_requests_sign_with_the_application_key_and_secret(tmp_path):
    from app.services.label_sync.clients import DiscogsClient

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        return httpx.Response(200, json={"id": 7})

    client = DiscogsClient("app-key", "app-secret", HttpCache(tmp_path / "cache.db"),
                           httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)

    assert client.get("/labels/7") == {"id": 7}
    assert seen == ["Discogs key=app-key, secret=app-secret"]


def test_summary_message_lists_the_outcome():
    summary = LabelSyncSummary(checked=3, found=1, not_found=1, errors=1, images=1, descriptions=1)

    assert summary.message() == "Checked 3 labels: found 1 (images 1, descriptions 1), not found 1, errors 1"


def test_unreadable_file_has_no_barcode(tmp_path):
    from app.services.label_sync.tags import read_barcode

    path = tmp_path / "broken.flac"
    path.write_bytes(b"not audio at all")

    assert read_barcode(path) is None
    assert read_barcode(tmp_path / "missing.flac") is None


def test_admin_runs_the_sync_as_a_background_job(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    add_track(store, tmp_path, "A", 1, label="Found")
    monkeypatch.setattr("app.api.label_sync.new_http_client",
                        lambda: httpx.Client(transport=beatport_login_transport()))
    monkeypatch.setattr("app.services.label_sync.job.build_resolver",
                        lambda *args: ScriptedResolver({"Found": LabelResult(external_ids={"discogs": "7"})}))
    client = TestClient(app)
    client.post("/api/v1/label-sync/beatport", json={"username": "me", "password": "pw"})
    client.put("/api/v1/label-sync/discogs", json=DISCOGS_APP)

    started = client.post("/api/v1/jobs/label-sync", json={})

    assert started.status_code == 200
    job_id = started.json()["job_id"]
    with JOBS_LOCK:
        job = JOBS[job_id]
    # BackgroundTasks у TestClient выполняются до возврата ответа.
    assert (job.status, job.message) == ("completed", "Checked 1 labels: found 1 (images 0, descriptions 0), not found 0, errors 0")
    assert label_state(store, "Found") == ("found", "7")
    status = client.get("/api/v1/label-sync").json()
    assert status["labels"]["found"] == 1
    # Итог лежит в базе, а не только в задаче: его видно в панели и после перезапуска.
    assert {k: status["last_run"][k] for k in ("mode", "status", "message")} == {
        "mode": "sync", "status": "completed", "message": job.message,
    }
    assert status["last_run"]["finished_at"] is not None


def test_run_cut_by_a_restart_is_shown_as_interrupted(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    store.start_label_sync_run("retry")  # задача была только в памяти — сервер перезапустился

    last_run = TestClient(app).get("/api/v1/label-sync").json()["last_run"]

    assert (last_run["mode"], last_run["status"]) == ("retry", "interrupted")
    assert last_run["message"] == "Interrupted by a server restart"


def test_run_mode_names_the_kind_of_run():
    from app.services.label_sync.job import run_mode

    assert run_mode(False, None, False) == "sync"
    assert run_mode(True, None, False) == "retry"
    assert run_mode(False, 5, False) == "label"
    assert run_mode(True, None, True) == "recheck"


def test_status_counts_labels_filled_by_the_old_script_before_the_first_run(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    add_track(store, tmp_path, "A", 1, label="Known")
    add_track(store, tmp_path, "B", 1, label="Unknown")
    add_track(store, tmp_path, "C", 1, label="Fresh")
    from app.models import LabelMetadata
    store.save_label_metadata(LabelMetadata(name="Known", external_ids={"discogs": "1"}))
    store.save_label_metadata(LabelMetadata(name="Unknown"))

    labels = TestClient(app).get("/api/v1/label-sync").json()["labels"]

    assert labels == {"found": 1, "not_found": 1, "self_released": 0, "error": 0, "pending": 1}


def test_official_name_comes_from_beatport_first_and_not_on_label_release_means_self_released():
    beatport = FakeApi({"/catalog/labels/44845/": {"name": "Trip Recordings"}})
    discogs = FakeApi({
        "/labels/771357": {"name": "трип"},
        "/labels/5": {"name": "Diynamic Music (2)"},
        "/database/search?barcode=111&per_page=5&type=release": {"results": [{"id": 1}]},
        "/releases/1": {"labels": [{"id": 750, "name": "Not On Label (Shura Self-released)"}]},
        "/database/search?barcode=222&per_page=5&type=release": {"results": [{"id": 2}]},
        "/releases/2": {"labels": [{"id": 750, "name": "Not On Label"}, {"id": 9, "name": "Koala Music"}]},
    })
    resolver = LabelResolver(beatport, discogs, FakeWeb({}))

    assert resolver.official_name("44845", "771357") == "Trip Recordings"  # Discogs даже не спрашивали
    assert "/labels/771357" not in discogs.calls
    assert resolver.official_name(None, "5") == "Diynamic Music"
    assert resolver.discogs_not_on_label(["111"]) is True
    assert resolver.discogs_not_on_label(["222"]) is False  # у релиза есть и настоящий лейбл

    result = resolver.resolve("Shura", ["111"], [], lambda: [])
    assert not result.found and result.self_released


def test_run_marks_self_released_labels_fills_official_names_and_merges_duplicates(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    add_track(store, tmp_path, "A", 1, label="919813 Records DK")
    add_track(store, tmp_path, "B", 1, label="Shura Music")
    add_track(store, tmp_path, "C", 1, label="trip recordings")
    add_track(store, tmp_path, "D", 1, label="Nina's Trip Label")
    from app.config import Settings
    from app.models import LabelMetadata
    settings = Settings.from_env()
    # Найден до того, как стали запоминать официальное название.
    store.save_label_metadata(LabelMetadata(name="trip recordings", external_ids={"beatport": "44845"}))
    store.set_label_sync_state(store.label_id_by_name("trip recordings"), "found", keys_hash=None, beatport_id="44845")
    resolver = ScriptedResolver({
        "Shura Music": LabelResult(self_released=True),
        "Nina's Trip Label": LabelResult(external_ids={"beatport": "44845"}, official_name="Trip Recordings"),
    })
    resolver.names["44845"] = "Trip Recordings"

    summary = run_label_sync(store, settings, resolver, workers=1, read=lambda path: None)

    assert resolver.calls.count("919813 Records DK") == 0  # самиздат по названию даже не ищется
    assert (summary.found, summary.not_found, summary.self_released) == (1, 0, 2)
    assert (summary.merged, summary.renamed) == (1, 1)
    assert "self-released 2" in summary.message() and "Duplicates merged 1, renamed 1" in summary.message()
    labels = TestClient(app).get("/api/v1/label-sync").json()["labels"]
    assert labels == {"found": 1, "not_found": 0, "self_released": 2, "error": 0, "pending": 0}
    names = [label.name for label in store.list_labels(limit=10, offset=0)[0]]
    assert names == ["Trip Recordings", "919813 Records DK", "Shura Music"]

    # Самиздат больше не ищется, в том числе по кнопке «ещё раз ненайденные».
    resolver.calls.clear()
    run_label_sync(store, settings, resolver, retry_not_found=True, workers=1, read=lambda path: None)
    assert resolver.calls == []
