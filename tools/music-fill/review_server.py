"""Локальная страница разбора кандидатов: python review_server.py → http://127.0.0.1:8765

/            — разбор (артисты, альбомы, плейлисты, видео)
/decisions   — принятые решения, план загрузки, отправка в deemix, менеджер загрузок

Читает out/* (пересобираются build.py). Решения — decisions.json, отправленное в deemix —
downloads.json; оба лежат отдельно от out/, пересборка их не трогает.
Ответы Deezer API кэшируются в cache/deezer.json, Discogs — в cache/discogs.json,
Beatport — в cache/beatport.json (вход: python beatport_login.py).
"""

import csv
import os
import json
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

import build
import beatport
import discogs
import journal
import slsk
import tagger
from journal import event

ROOT = Path(__file__).parent
OUT = ROOT / "out"
DECISIONS = ROOT / "decisions.json"
DOWNLOADS = ROOT / "downloads.json"
CACHE = ROOT / "cache" / "deezer.json"
PORT = 8765
DEEMIX = "http://127.0.0.1:6595"
DEFAULT_TYPES = ("album", "ep", "single")  # типы релизов для «дискографии»; меняются на странице плана

lock = threading.Lock()


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def read_csv(name: str) -> list[dict]:
    with open(OUT / name, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


NUMERIC = {"interest", "lib_albums", "lib_songs", "lf_total", "lf_2008_14", "lf_2015_24", "lf_2025_",
           "lf_tracks", "lf_loved", "ytm_likes", "ytm_lib_artist", "ytm_sub", "ytm_lib_songs",
           "ytm_lib_albums", "ytm_history", "lf_plays", "ytm_lib"}


def typed(rows: list[dict]) -> list[dict]:
    for r in rows:
        for k in NUMERIC & r.keys():
            r[k] = int(r[k] or 0)
    return rows


def load_data() -> dict:
    artists = [r for r in typed(read_csv("artists.csv")) if r["status"] == "missing" and r["interest"] >= 3]
    ugc = read_csv("ugc_unparsed.csv")
    for u in ugc:
        u["key"] = u["videoId"]
    return {
        "artists": artists,
        "albums": typed(read_csv("albums_missing.csv")),
        "ugc": ugc,
        "playlists": read_json(OUT / "playlists.json", []),
        "decisions": read_json(DECISIONS, {}),
        "matches": matches,
    }


# ---------- Deezer ----------

cache = read_json(CACHE, {})


_dz_local = threading.local()


def _dz_session() -> requests.Session:
    """Своя сессия на поток: соединение с api.deezer.com переиспользуется (без нового TLS на запрос)."""
    if not hasattr(_dz_local, "s"):
        _dz_local.s = requests.Session()
    return _dz_local.s


def deezer(path: str, **params) -> dict:
    url = f"https://api.deezer.com/{path}?{urllib.parse.urlencode(params)}"
    with lock:
        if url in cache:
            return cache[url]
    for attempt in range(6):
        data = _dz_session().get(url, timeout=20).json()
        if data.get("error", {}).get("code") == 4:  # quota: 50 запросов / 5 с
            time.sleep(1 + attempt)
            continue
        break
    if "error" in data:  # «не найдено» не запоминаем — релиз может появиться на Deezer позже
        return data
    with lock:
        cache[url] = data
        _cache_dirty[0] += 1
        if _cache_dirty[0] >= 25:  # файл большой — пишем пачками, а не на каждый запрос
            write_json(CACHE, cache)
            _cache_dirty[0] = 0
    return data


_cache_dirty = [0]


def flush_cache() -> None:
    with lock:
        if _cache_dirty[0]:
            write_json(CACHE, cache)
            _cache_dirty[0] = 0


def search_artists(name: str) -> list[dict]:
    """Deezer-артисты по имени, с учётом совпадения имени, а не позиции в выдаче.

    Поиск Deezer нечёткий и ранжирует по популярности: для 'mininome' нужный артист — 10-й из 21,
    после Minnie Riperton и The Minions. Поэтому: сначала artist:"…" (только артисты, в чьём имени
    есть эта фраза), затем обычный поиск с limit=50; наверх — точное совпадение имени, потом похожее
    (транслит, соавторы), внутри группы — по числу фанатов; остальное — в порядке Deezer."""
    phrase = name.replace('"', " ").strip()
    found, seen = [], set()
    for params in ({"q": f'artist:"{phrase}"', "limit": 25}, {"q": name, "limit": 50}):
        for a in deezer("search/artist", **params).get("data", []):
            if a["id"] not in seen:
                seen.add(a["id"])
                found.append(a)
    key = build.norm(name)

    def rank(ia):
        i, a = ia
        exact = build.norm(a["name"]) == key
        similar = not exact and build.artists_similar(a["name"], name)
        return (0 if exact else 1 if similar else 2, -(a.get("nb_fan") or 0) if (exact or similar) else i)

    return [a for _, a in sorted(enumerate(found), key=rank)]


def deezer_artist(q: str) -> dict:
    found = search_artists(q)[:8]
    return {"candidates": [{k: a.get(k) for k in ("id", "name", "picture_medium", "nb_album", "nb_fan", "link")}
                           | {"exact": build.norm(a["name"]) == build.norm(q)} for a in found]}


def deezer_album(q: str) -> dict:
    found = deezer("search/album", q=q, limit=5).get("data", [])
    return {"candidates": [{"id": a["id"], "title": a["title"], "artist": a["artist"]["name"],
                            "artist_id": a["artist"]["id"],
                            "cover": a.get("cover_medium"), "nb_tracks": a.get("nb_tracks"),
                            "link": a.get("link")} for a in found]}


def deezer_track(q: str, artist: str = "", title: str = "") -> dict:
    found = deezer("search/track", q=q, limit=10).get("data", [])
    if not found and artist and title:
        # 'Noise feat. Lelah (Original Mix)' → 'Noise': поиск Deezer не любит хвосты
        found = deezer("search/track", q=f"{artist} {build.norm_title(title)}", limit=10).get("data", [])
    if artist:
        # поиск Deezer часто ставит первым чужой трек — наверх тех, где совпадает артист
        from difflib import SequenceMatcher
        a_key, t_key = build.norm(artist), build.norm_title(title)
        found.sort(key=lambda t: (build.norm(t["artist"]["name"]) != a_key,
                                  -SequenceMatcher(None, t_key, build.norm_title(t["title"])).ratio()))
    return {"candidates": [{"id": t["id"], "title": t["title"], "artist": t["artist"]["name"],
                            "artist_id": t["artist"]["id"], "album": t["album"]["title"],
                            "album_id": t["album"]["id"], "cover": t["album"].get("cover_small"),
                            "preview": t.get("preview"), "link": t.get("link")} for t in found[:5]]}


# ---------- план загрузки ----------

_library = None


def library():
    global _library
    if _library is None:
        artist_albums, _, _, appears = build.library_index()
        _library = {"albums": artist_albums, "appears": appears}
    return _library


def in_library(artist: str, album: str, strict: bool = False) -> bool:
    """Свои релизы — нестрогое сопоставление названий (издания, скобки, обрезки last.fm).
    strict — для участия и сборников: точное название (без учёта регистра и пунктуации)
    и артист есть на этом альбоме хотя бы одним треком: сборник записан на Various Artists."""
    if strict:
        return build.words(album) in library()["appears"].get(build.norm(artist), ())
    alb = build.norm_album(album)
    return any(build.album_matches(alb, la) for la in library()["albums"].get(build.norm(artist), ()))


DISCO_CACHE = ROOT / "cache" / "discography.json"
DISCO_TTL = 7 * 86400  # новые релизы — отдельная задача; для плана недельной давности хватает
_disco_cache: dict[str, dict] = read_json(DISCO_CACHE, {})


def discography_tabs(artist_id: int) -> dict:
    """Дискография как на сайте Deezer (и как её видит сам deemix): вкладки album/ep/single/
    compile/featured. Публичный api.deezer.com отдаёт урезанный список, поэтому берём у deemix.
    Хранится на диске: план не должен заново опрашивать deemix при каждом открытии страницы."""
    key = str(artist_id)
    with lock:
        hit = _disco_cache.get(key)
    if hit and time.time() - hit["ts"] < DISCO_TTL:
        return hit["data"]
    try:
        deemix.login()
        r = deemix.s.get(f"{DEEMIX}/api/getTracklist", params={"type": "artist", "id": artist_id}, timeout=120)
    except requests.ConnectionError:
        raise RuntimeError("deemix не запущен — полная дискография берётся через него; запусти deemix-gui "
                           "(уже открытые раньше дискографии лежат в кэше и работают без него)") from None
    r.raise_for_status()
    data = r.json()
    if "releases" not in data:  # ошибку не кэшируем
        return data
    with lock:
        _disco_cache[key] = {"ts": time.time(), "data": data}
        write_json(DISCO_CACHE, _disco_cache)
    return data


TYPE_LABEL = {"album": "альбом", "ep": "EP", "single": "сингл", "compile": "сборник", "featured": "участие", "more": "ещё"}


TYPE_ORDER = ["album", "ep", "single", "compile", "featured", "more"]


def discography(artist_id: int) -> list[dict]:
    """Все релизы артиста (как на сайте Deezer) с пометками:
    have — уже есть в Navidrome; dup — повтор уже встреченного названия (другое издание)."""
    data = discography_tabs(artist_id)
    name = data.get("name", "")
    out, seen, ids = [], {}, set()
    for rtype in TYPE_ORDER:
        for a in sorted(data.get("releases", {}).get(rtype, []), key=lambda a: a.get("release_date") or ""):
            if a["id"] in ids:
                continue  # один релиз бывает в двух вкладках (album и more) — иначе уйдёт в deemix дважды
            ids.add(a["id"])
            exact = build.words(a["title"])
            out.append({"id": a["id"], "title": a["title"], "type": rtype,
                        "year": (a.get("release_date") or "")[:4], "date": a.get("release_date") or "",
                        "cover": a.get("cover_small") or f"https://api.deezer.com/album/{a['id']}/image",
                        "url": f"https://www.deezer.com/album/{a['id']}",
                        # «Сборники» Deezer — чаще свои (артист альбома, как у своих релизов: там
                        # «(DJ Mix)» и прочие хвосты — норма), но бывают и на Various Artists;
                        # «Участие» — только строго, см. in_library
                        "have": (rtype != "featured" and in_library(name, a["title"]))
                                or (rtype in ("featured", "compile") and in_library(name, a["title"], strict=True)),
                        "dup": seen.get(exact)})
            seen.setdefault(exact, f"{a.get('release_date', '')[:4]} {a['title']}")
    return out


def discography_view(artist_id: int) -> dict:
    data = discography_tabs(artist_id)
    return {"name": data.get("name"), "link": f"https://www.deezer.com/artist/{artist_id}",
            "releases": discography(artist_id)}


def plan_item(key: str, d: dict, types: set[str]) -> dict:
    """Решение → что уходит в deemix: [{url, label}] + пропущенное (уже есть)."""
    kind, act = d["kind"], d["decision"]
    item = {"key": key, "kind": kind, "decision": act, "name": d.get("name"), "urls": [], "skipped": [],
            "releases": [], "problem": None, "artist_link": None}
    artist_id, album_id, track_id = d.get("artist_id"), d.get("album_id"), d.get("track_id")
    if kind == "artists":
        artist_id = artist_id or d.get("deezer_id")
    elif kind == "albums":
        album_id = album_id or d.get("deezer_id")
    artist_name, track_title = decision_artist_title(d)
    item["search"] = f"https://www.deezer.com/search/{urllib.parse.quote(f'{artist_name} {track_title}'.strip())}"

    if artist_id:
        item["artist_link"] = f"https://www.deezer.com/artist/{artist_id}"
    if act == "label":
        label_plan_item(item, d, types)
    elif act == "discography":
        if not artist_id:
            item["problem"] = f"артист «{artist_name}» на Deezer не найден"
            return item
        # releases — весь список для галочек; urls — то, что отмечено по умолчанию
        for a in discography(artist_id):
            a["label"] = f"{a['year']} {a['title']} · {TYPE_LABEL.get(a['type'], a['type'])}"
            a["checked"] = a["type"] in types and not a["have"] and not a["dup"]
            item["releases"].append(a)
            if a["checked"]:
                item["urls"].append({"url": a["url"], "label": a["label"]})
            elif a["have"]:
                item["skipped"].append({"url": a["url"], "label": a["label"]})
    elif act in ("album", "download") and kind in ("artists", "search") and not album_id:
        top = deezer(f"artist/{artist_id}/top", limit=10).get("data", []) if artist_id else []
        counts: dict[int, list] = {}
        for t in top:  # «лучший альбом» — тот, из которого больше всего топ-треков
            counts.setdefault(t["album"]["id"], []).append(t["album"]["title"])
        if not counts:
            item["problem"] = "не выбран артист на Deezer"
        else:
            aid = max(counts, key=lambda k: len(counts[k]))
            with_discography(item, artist_id, aid, counts[aid][0])
    elif act in ("album", "download") and album_id:
        title = (d.get("album_title") or d.get("name") or "").split(" — ", 1)[-1]
        with_discography(item, artist_id, album_id, title)
    elif act == "track" and track_id:
        single(item, f"https://www.deezer.com/track/{track_id}", d.get("name"), "track", album_id)
        with_discography(item, artist_id, None, None)
    else:
        item["problem"] = "на Deezer не нашлось"
    return item


def with_discography(item: dict, artist_id, album_id, album_title) -> None:
    """Альбом/трек показываем на фоне всей дискографии артиста: выбранный релиз отмечен,
    остальное — нет (можно доотметить). Выбранный альбом ищем в дискографии по id или названию;
    если его там нет (другое издание) — добавляем отдельной строкой."""
    releases = discography(artist_id) if artist_id else []
    target = None
    if album_id:
        target = next((r for r in releases if str(r["id"]) == str(album_id)), None) or \
                 next((r for r in releases if build.words(r["title"]) == build.words(album_title)), None)
        if not target:
            single(item, f"https://www.deezer.com/album/{album_id}", album_title, "album", album_id)
    for r in releases:
        r["label"] = f"{r['year']} {r['title']} · {TYPE_LABEL.get(r['type'], r['type'])}"
        r["checked"] = r is target and not r["have"]  # уже есть в библиотеке — не отмечаем
        item["releases"].append(r)
        if r["checked"]:
            item["urls"].append({"url": r["url"], "label": r["label"]})


def decision_artist_title(d: dict) -> tuple[str, str]:
    """Артист и название из решения — для ссылки «искать на Deezer»."""
    if d.get("artist"):
        return d["artist"], d.get("title", "")
    name = d.get("name") or ""
    if d.get("kind") == "artists":
        return name, ""
    artist, _, title = name.partition(" — ")
    return artist, title


def find_artist(name: str) -> dict | None:
    """Deezer-артист по имени: сначала точное совпадение, потом то же имя в другом написании
    (транслит, соавторы). Чужого артиста не подставляем — лучше None."""
    for a in search_artists(name)[:3]:  # отсортировано: точное имя, потом похожее
        if build.norm(a["name"]) == build.norm(name) or build.artists_similar(a["name"], name):
            return {"id": a["id"], "name": a["name"], "link": a["link"], "nb_album": a.get("nb_album")}
    return None





def single(item: dict, url: str, label: str, rtype: str, album_id) -> None:
    """Решение на один релиз/трек — в том же формате, что и строка дискографии."""
    cover = f"https://api.deezer.com/album/{album_id}/image" if album_id else None
    item["releases"].append({"url": url, "label": label, "title": label, "type": rtype, "cover": cover,
                             "have": False, "dup": None, "checked": True})
    item["urls"].append({"url": url, "label": label})


DOWNLOADABLE = {"discography", "album", "download", "track", "label"}


# ---------- «откуда взялось» и «что есть в библиотеке» ----------

CYR_LAT = str.maketrans({"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z",
                         "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
                         "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch",
                         "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"})


def translit_key(name: str) -> str:
    """Ключ для поиска похожих имён: транслит + без пробелов ('ГDР' ~ 'ГДР' ~ 'GDR')."""
    return build.norm(name).translate(CYR_LAT)


_events = None
_lib = None


def events_by_artist() -> dict[str, list[dict]]:
    global _events
    if _events is None:
        _events = {}
        with open(OUT / "events.csv", encoding="utf-8-sig") as f:
            for e in csv.DictReader(f):
                _events.setdefault(build.norm(e["artist"]), []).append(e)
    return _events


def lib_catalog() -> dict:
    """Navidrome: артист (ключ) → {name, albums[{name, year, songs}]}, плюс индекс по транслиту."""
    global _lib
    if _lib is None:
        by_key: dict[str, dict] = {}
        for a in build.load("navidrome/albums.json"):
            name = build.fix_mojibake(a.get("artist", ""))
            names = {name} | {build.fix_mojibake(x.get("name", "")) for x in a.get("artists") or []}
            for n in filter(None, names):
                e = by_key.setdefault(build.norm(n), {"name": n, "albums": []})
                e["albums"].append({"name": build.fix_mojibake(a.get("name")), "year": a.get("year"),
                                    "songs": a.get("songCount")})
        translit: dict[str, list[str]] = {}
        for k, e in by_key.items():
            translit.setdefault(translit_key(e["name"]), []).append(k)
        _lib = {"by_key": by_key, "translit": translit}
    return _lib


def library_for(artist: str) -> dict:
    lib = lib_catalog()
    key = build.norm(artist)
    exact = lib["by_key"].get(key)
    tk = translit_key(artist)
    similar = []
    if len(tk) >= 3:
        from difflib import get_close_matches
        for m in get_close_matches(tk, lib["translit"].keys(), n=5, cutoff=0.8):
            for k in lib["translit"][m]:
                if k != key:
                    e = lib["by_key"][k]
                    similar.append({"name": e["name"], "albums": len(e["albums"]), "titles": sorted({a["name"] for a in e["albums"]})[:8]})
    # имя, в котором артист встречается как часть: 'Горшок' → 'Михаил «Горшок» Горшенёв'.
    # Если же это склейка соавторов ('Gush • Husbands • … • The Cinematic Orchestra • …' — так
    # бывает записан сборник) и артист — одна из её частей, это не «похожее имя», а совместная запись
    joint = []
    mine = build.artist_parts(artist)
    if len(key) >= 4:
        for k, e in lib["by_key"].items():
            if key not in k or k == key or any(s["name"] == e["name"] for s in similar):
                continue
            parts = build.artist_parts(e["name"])
            titles = sorted({a["name"] for a in e["albums"]})[:8]
            if len(parts) >= 2 and mine and mine <= parts:
                joint.append({"others": len(parts - mine), "albums": len(e["albums"]), "titles": titles})
            elif len(similar) < 8:
                similar.append({"name": e["name"], "albums": len(e["albums"]), "titles": titles})
    return {"exact": sorted(exact["albums"], key=lambda a: a["year"] or 0) if exact else [],
            "similar": similar, "joint": joint[:8]}


def evidence(kind: str, key: str) -> dict:
    akey, _, alb = key.partition("|")
    evs = events_by_artist().get(akey, [])
    if kind == "albums":
        evs = [e for e in evs if build.norm_album(e["album"]) == alb]
    from collections import Counter
    lf = [e for e in evs if e["source"] == "lf_scrobble"]
    years = sorted(int(e["ts"]) for e in lf if e["ts"])
    fmt = lambda c: [{"name": k, "n": n} for k, n in c.most_common(15)]
    artist = evs[0]["artist"] if evs else akey
    return {
        "lastfm": {"total": len(lf),
                   "first": time.strftime("%Y-%m", time.localtime(years[0])) if years else None,
                   "last": time.strftime("%Y-%m", time.localtime(years[-1])) if years else None,
                   "tracks": fmt(Counter(e["title"] for e in lf)),
                   "albums": fmt(Counter(e["album"] for e in lf if e["album"])),
                   "loved": [e["title"] for e in evs if e["source"] == "lf_loved"]},
        "ytm": {"likes": [{"title": e["title"], "album": e["album"], "via": e["extra"]}
                          for e in evs if e["source"] in ("ytm_like", "ytm_like_ugc")],
                "library_songs": [{"title": e["title"], "album": e["album"]} for e in evs if e["source"] == "ytm_lib_song"],
                "library_albums": [e["album"] for e in evs if e["source"] == "ytm_lib_album"],
                "library_artist": any(e["source"] == "ytm_lib_artist" for e in evs),
                "subscribed": any(e["source"] == "ytm_subscription" for e in evs),
                "history": [e["title"] for e in evs if e["source"] == "ytm_history"]},
        "library": library_for(artist),
    }


# ---------- ручной поиск (вкладка «Поиск») ----------

_songs_by_title = None


def lib_track_have(artist: str, title: str) -> str | None:
    """Есть ли трек в Navidrome: то же название (с транслитом) у похожего артиста → 'трек — альбом'."""
    global _songs_by_title
    if _songs_by_title is None:
        _songs_by_title = {}
        for s in lib_songs():
            _songs_by_title.setdefault(s["tkey"], []).append(s)
    for s in _songs_by_title.get(build.title_key(title), ()):
        if build.artists_similar(artist, s["artist"]):
            return f"{s['title']} — {s['album']}"
    return None


def lib_artist_summary(name: str) -> dict:
    lib = library_for(name)
    return {"albums": len(lib["exact"]), "similar": [s["name"] for s in lib["similar"]]}


def search_all(q: str) -> dict:
    """Поиск по Deezer (оттуда качает deemix) с пометками, что уже есть в библиотеке."""
    artists = search_artists(q)[:10]
    albums = deezer("search/album", q=q, limit=12).get("data", [])
    tracks = deezer("search/track", q=q, limit=15).get("data", [])
    return {
        "artists": [{"type": "artist", "id": a["id"], "title": a["name"], "artist": a["name"], "artist_id": a["id"],
                     "cover": a.get("picture_medium"), "nb_album": a.get("nb_album"), "nb_fan": a.get("nb_fan"),
                     "link": a.get("link"), "lib": lib_artist_summary(a["name"])} for a in artists],
        "albums": [{"type": "album", "id": a["id"], "title": a["title"], "artist": a["artist"]["name"],
                    "artist_id": a["artist"]["id"], "album_id": a["id"], "cover": a.get("cover_medium"),
                    "nb_tracks": a.get("nb_tracks"), "record_type": a.get("record_type"), "link": a.get("link"),
                    "have": in_library(a["artist"]["name"], a["title"])} for a in albums],
        "tracks": [{"type": "track", "id": t["id"], "title": t["title"], "artist": t["artist"]["name"],
                    "artist_id": t["artist"]["id"], "album_id": t["album"]["id"], "album": t["album"]["title"],
                    "cover": t["album"].get("cover_medium"), "link": t.get("link"), "duration": t.get("duration"),
                    "have": lib_track_have(t["artist"]["name"], t["title"])} for t in tracks],
        "rates": size_rates(),
    }


def related_artists(artist_id: int) -> list[dict]:
    """Похожие артисты Deezer с пометкой «есть в библиотеке»."""
    return [{"id": a["id"], "name": a["name"], "cover": a.get("picture_medium"), "nb_fan": a.get("nb_fan"),
             "link": a.get("link"), "lib": lib_artist_summary(a["name"])}
            for a in deezer(f"artist/{artist_id}/related", limit=12).get("data", [])]


# ---------- Discogs / Beatport: подтверждение релизов артиста ----------

def discogs_confirm(artist_id: int) -> dict:
    """Какие релизы Deezer-дискографии есть у этого артиста на Discogs. Только пометка:
    релиза может не быть на Discogs и без подвоха (свежие цифровые синглы) — галки не трогаем."""
    data = discography_tabs(artist_id)
    rels = discography(artist_id)
    artist = discogs.match_artist(artist_id, data.get("name", ""), [r["title"] for r in rels])
    if not artist:
        return {"artist": None, "confirmed": {}}
    idx = discogs.release_index(discogs.artist_releases(artist["id"]))
    confirmed = {}
    for r in rels:
        hit = next((idx[k] for k in discogs.title_keys(r["title"]) if k in idx), None)
        if hit:
            confirmed[str(r["id"])] = discogs.release_url(hit)
    discogs.flush()
    return {"artist": artist, "confirmed": confirmed, "total": len(rels)}


def label_matches(deezer_label: str, labels: list[str]) -> bool:
    """Поле label альбома Deezer пишут как угодно ('Foreign Family Collective/Ninja Tune') —
    достаточно, чтобы имя лейбла входило в него целыми словами."""
    have = f" {build.words(deezer_label)} "
    return any(f" {build.words(discogs.clean_name(l))} " in have for l in labels if build.words(l))


def deezer_find_release(artists: list[str], titles: list[str], labels: list[str]) -> dict | None:
    """Релиз из каталога лейбла → альбом на Deezer, когда UPC не помог (у эксклюзива Beatport
    'TRP054bp' свой UPC, на Deezer — общее издание с другим). Название — по ключам
    discogs.title_keys (только равенство), артист — любой из артистов релиза: Beatport пишет
    'Justin Jay, Alex Wilcox', Deezer — только 'Alex Wilcox'. Сборник Various Artists на Deezer
    часто записан на первого участника, а названия шаблонные ('Various Artists #1') — для него
    артист не проверить, поэтому сверяем лейбл альбома."""
    va = any(discogs.is_various(a) for a in artists)
    names = [discogs.clean_name(a) for a in artists if a.strip()]
    queries = []
    parts = dict.fromkeys(p for t in titles for p in discogs.title_parts(t))
    for p in list(parts):
        # поиск Deezer не находит ничего по 'B.E.R (Extended Mix)' — а по 'B.E.R' находит
        bare = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", p).strip()
        # Beatport дописывает гостя и артиста в название: 'Voices Ft. Charli Brix', 'Moby - The Drum & Bass Remixes'
        bare = re.sub(r"\s+(?:ft|feat|featuring)\.?\s.*$", "", bare, flags=re.I).strip()
        for n in names:
            if bare.lower().startswith(n.lower() + " - "):
                bare = bare[len(n) + 3:].strip()
        # 'Acid Wall EP' — на Deezer 'Acid Wall', и поиск с хвостом EP его не находит
        bare = re.sub(r"\s+(?:EP|LP)$", "", bare, flags=re.I).strip()
        if bare and bare != p:
            parts.setdefault(bare)
    # названия сравниваются по тем же вариантам — артист при этом обязан совпасть (ниже)
    want = set().union(*(discogs.title_keys(t) for t in parts))
    for p in list(parts):  # кириллица на Deezer часто записана транслитом
        tr = build.words(p).translate(build.CYR_LAT)
        if tr != build.words(p):
            parts.setdefault(tr)
    for t in sorted(parts, key=lambda p: not p.isascii()):  # 'Резонанс = Resonance' — каждый вариант, латиница первой
        if va or not names:
            queries += [f'album:"{t}"', t]
        else:
            queries += [q for n in names for q in (f'artist:"{n}" album:"{t}"', f"{n} {t}")]
    # последним — артист + начало названия: 'City Cuts Remixes Part 1' Deezer не ищет, а 'Dosem City
    # Cuts' находит 'City Cuts (Remixes, Pt. 1)'. Отбор всё равно по строгому сравнению названий ниже
    if names and not va:
        for t in parts:
            w = build.words(t).split()
            if len(w) >= 3:
                queries += [f"{n} {' '.join(w[:2])}" for n in names]
    for q in dict.fromkeys(queries):
        for a in deezer("search/album", q=q, limit=15).get("data", []):
            if not (discogs.title_keys(a["title"]) & want):
                continue
            if va or not names:
                if label_matches(deezer(f"album/{a['id']}").get("label", ""), labels):
                    return a
            elif any(discogs.same_artist(a["artist"]["name"], n) for n in names):
                return a
    return None


def find_in_discography(artists: list[str], title: str) -> dict | None:
    """Последний шанс: артист на Deezer → его полная дискография (как на сайте, через deemix) →
    релиз с тем же названием (строгие ключи). Поиск Deezer капризен ('Adexyl EP' не находит вообще),
    а дискография артиста — полный список. Тёзку отсекает то же строгое совпадение названия."""
    want = discogs.title_keys(re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", title).strip() or title) | discogs.title_keys(title)
    for name in artists:
        art = find_artist(discogs.clean_name(name)) if name.strip() else None
        if not art:
            continue
        try:
            rels = discography(art["id"])
        except RuntimeError:  # deemix не запущен — без дискографии
            return None
        for x in rels:
            if discogs.title_keys(x["title"]) & want:
                return {"id": int(x["id"]), "title": x["title"], "artist": {"name": art["name"]},
                        "cover_small": x["cover"], "link": x["url"],
                        "record_type": {"album": "album", "ep": "ep", "single": "single",
                                        "compile": "compile"}.get(x["type"], "album")}
    return None


def deezer_releases_with_upc(artist_id: int) -> list[dict]:
    """Дискография Deezer с UPC (album/{id} — по запросу на релиз, кэш Deezer бессрочный)."""
    out = []
    for r in discography(artist_id):
        out.append({"id": r["id"], "title": r["title"], "upc": deezer(f"album/{r['id']}").get("upc")})
    flush_cache()
    return out


def beatport_confirm(artist_id: int) -> dict:
    """То же, что discogs_confirm, по Beatport: охват — электроника, зато полный и с UPC."""
    data = discography_tabs(artist_id)
    rels = deezer_releases_with_upc(artist_id)
    artist = beatport.match_artist(artist_id, data.get("name", ""), rels)
    if not artist:
        return {"artist": None, "confirmed": {}}
    confirmed = beatport.confirm(rels, beatport.artist_releases(artist["id"]))
    beatport.flush()
    return {"artist": artist, "confirmed": confirmed, "total": len(rels)}


# ---------- лейблы: каталог Beatport → Deezer по UPC ----------

LABELS = ROOT / "cache" / "labels.json"  # готовые каталоги лейблов (Beatport + найденное на Deezer)
label_jobs: dict[str, dict] = {}


def build_label(label_id: int, progress: dict) -> dict:
    info = beatport.label(label_id)
    releases = beatport.label_releases(label_id)
    progress["total"] = len(releases)
    out = []
    for r in sorted(releases, key=lambda r: (r.get("new_release_date") or "", r.get("catalog_number") or "")):
        a, how = None, None
        code = beatport.upc(r.get("upc"))
        if code:
            found = deezer(f"album/upc:{code}")
            if "error" not in found:
                a, how = found, "upc"
        if not a:  # без UPC или его нет на Deezer — поиск по названию (строгий, как для Discogs)
            artists = ["Various"] if beatport.is_various(r) else beatport.artists_of(r)
            a = deezer_find_release(artists, [r.get("name", "")], [info.get("name", "")])
            how = "название" if a else None
        if not a and not beatport.is_various(r):  # поиск Deezer мимо — сверка с дискографией артиста
            a = find_in_discography(beatport.artists_of(r), r.get("name", ""))
            how = "дискография" if a else None
        va = beatport.is_various(r)
        rtype = {"album": "album", "ep": "ep", "single": "single", "compile": "compile"}.get(
            (a or {}).get("record_type"), beatport.guess_type(r))
        if va:
            rtype = "compile"
        elif re.search(r"\bep\b", build.words(r.get("name", ""))):
            rtype = "ep"  # Deezer пишет EP на 4–5 треков как album — название с Beatport точнее
        lib_artist = "Various Artists" if va else (a["artist"]["name"] if a else beatport.display_artist(r))
        out.append({"artist": beatport.display_artist(r), "title": r.get("name", ""),
                    "year": (r.get("new_release_date") or "")[:4], "date": r.get("new_release_date") or "", "type": rtype,
                    "catno": r.get("catalog_number") or "", "format": f"{r.get('track_count') or '?'} тр.",
                    "src": beatport.release_url(r), "upc": code,
                    "deezer": {"id": a["id"], "title": a["title"], "artist": a["artist"]["name"],
                               "cover": a.get("cover_small"), "link": a.get("link") or f"https://www.deezer.com/album/{a['id']}",
                               "record_type": a.get("record_type"), "how": how,
                               **album_length(a)} if a else None,
                    # у сборников названия шаблонные — «есть в библиотеке» только строго
                    "have": in_library(lib_artist, a["title"] if a else r.get("name", ""), strict=va)})
        progress["done"] += 1
    flush_cache()
    beatport.flush()
    return {"id": label_id, "name": info.get("name", ""), "releases": merge_editions(out),
            "url": f"https://www.beatport.com/label/{info.get('slug') or 'x'}/{label_id}"}


def album_length(a: dict) -> dict:
    """Длительность и число треков: в карточке (поиск по UPC) они уже есть, в выдаче поиска — нет."""
    if "duration" not in a:
        a = deezer(f"album/{a['id']}")
    return {"duration": a.get("duration"), "nb_tracks": a.get("nb_tracks")}


def merge_editions(releases: list[dict]) -> list[dict]:
    """Beatport заводит одно издание дважды (эксклюзив Beatport 'bp' и общая цифра 'd' под
    разными каталожными номерами). Склеиваем то, что ведёт на один альбом Deezer, а ненайденное —
    по артисту и названию; номера сохраняем все."""
    out, seen = [], {}
    for r in releases:
        k = ("dz", r["deezer"]["id"]) if r["deezer"] else ("t", build.norm(r["artist"]), build.words(r["title"]))
        if k in seen:
            first = seen[k]
            if r["catno"] and r["catno"] not in first["catno"]:
                first["catno"] = f"{first['catno']}, {r['catno']}" if first["catno"] else r["catno"]
            first["have"] = first["have"] or r["have"]
            continue
        seen[k] = r
        out.append(r)
    return out


def label_view(label_id: int, wait: bool = False) -> dict:
    """Каталог лейбла. Первый раз — фоном (сотни запросов), страница опрашивает прогресс."""
    key = f"bp:{label_id}"
    with lock:
        done = read_json(LABELS, {}).get(key)
    if done and time.time() - done["ts"] < DISCO_TTL:
        data = done["data"]
        missing = [r for r in data["releases"] if r["deezer"] and "duration" not in r["deezer"]]
        for r in missing:  # каталоги, собранные до подсчёта объёма: длительность — из кэша Deezer
            src = deezer(f"album/upc:{r['upc']}") if r["deezer"]["how"] == "upc" else {"id": r["deezer"]["id"]}
            r["deezer"].update(album_length(src))
        if missing:
            flush_cache()
            with lock:
                labels = read_json(LABELS, {})
                labels[key] = {"ts": done["ts"], "data": data}
                write_json(LABELS, labels)
        return {"status": "done", "rates": size_rates()} | data
    with lock:
        job = label_jobs.get(key)
        # сюда доходим, только когда готового свежего каталога нет: завершённая раньше сборка —
        # значит, он устарел или удалён; собираем заново (иначе — бесконечный label_view → label_view)
        if not job or job.get("error") or job.get("finished"):
            job = label_jobs[key] = {"done": 0, "total": 0}

            def run():
                try:
                    data = build_label(label_id, job)
                    with lock:
                        labels = read_json(LABELS, {})
                        labels[key] = {"ts": time.time(), "data": data}
                        write_json(LABELS, labels)
                    job["finished"] = True
                except Exception as exc:
                    job["error"] = repr(exc)
            job["thread"] = threading.Thread(target=run, daemon=True)
            job["thread"].start()
    if wait:
        job["thread"].join()
    if job.get("error"):
        return {"status": "error", "error": job["error"]}
    if job.get("finished"):
        return label_view(label_id)
    return {"status": "running", "done": job["done"], "total": job["total"]}


def label_plan_item(item: dict, d: dict, types: set[str]) -> None:
    lab = label_view(int(d["label_id"]), wait=True)
    if lab.get("status") != "done":
        item["problem"] = f"каталог лейбла не собрался: {lab.get('error')}"
        return
    found = [r for r in lab["releases"] if r["deezer"]]
    item["resolved"] = (f"Beatport: {len(lab['releases'])} релизов, на Deezer найдено {len(found)}, "
                        f"из них есть в библиотеке {sum(r['have'] for r in found)}")
    item["label_link"] = lab["url"]
    seen = set()
    for r in found:
        url = r["deezer"]["link"]
        if url in seen:
            continue
        seen.add(url)
        rel = {"id": r["deezer"]["id"], "url": url, "type": r["type"], "year": r["year"], "date": r.get("date", ""),
               "title": f"{r['artist']} — {r['title']}" + (f" [{r['catno']}]" if r["catno"] else ""),
               "cover": r["deezer"]["cover"], "have": r["have"], "dup": None,
               "src": r["src"], "src_name": "Beatport"}
        rel["label"] = f"{rel['year']} {r['artist']} — {r['title']} · {TYPE_LABEL.get(r['type'], r['type'])}"
        rel["checked"] = r["type"] in types | {"compile"} and not r["have"]
        item["releases"].append(rel)
        if rel["checked"]:
            item["urls"].append({"url": url, "label": rel["label"]})


# ---------- сопоставление треков плейлистов с Deezer: один раз, с сохранением ----------

MATCHES = ROOT / "matches.json"
matches: dict = read_json(MATCHES, {})  # videoId → {candidates, pick, ts}; единственная копия в памяти
match_state = {"running": False, "done": 0, "total": 0}


def save_matches() -> None:
    with lock:
        write_json(MATCHES, matches)
    flush_cache()


def tracks_to_match() -> list[dict]:
    """Треки плейлистов, которым нужно совпадение на Deezer (уже имеющиеся и удалённые — нет)."""
    seen, out = set(), []
    for pl in read_json(OUT / "playlists.json", []):
        for t in pl["tracks"]:
            if t["status"] in ("have_track", "gone") or t["key"] in seen:
                continue
            seen.add(t["key"])
            out.append(t)
    return out


def match_one(t: dict) -> dict:
    q = re.sub(r"[\[\(].*?[\]\)]", " ", f"{t['artist']} {t['title']}")
    res = deezer_track(q, t["artist"], t["title"])
    with lock:
        matches[t["key"]] = {"candidates": res["candidates"], "pick": 0, "ts": int(time.time())}
    return matches[t["key"]]


def match_tracks() -> None:
    """Фоновое досопоставление: ищет только тех, кого ещё нет в matches.json."""
    if match_state["running"]:
        return
    match_state["running"] = True
    try:
        todo = [t for t in tracks_to_match() if t["key"] not in matches]
        match_state.update(done=0, total=len(todo))
        for i, t in enumerate(todo, 1):
            try:
                match_one(t)
            except Exception:  # сеть/квота — этот трек попробуем при следующем запуске
                pass
            match_state["done"] = i
            if i % 50 == 0:
                save_matches()
        save_matches()
    finally:
        match_state["running"] = False


def start_matching() -> None:
    threading.Thread(target=match_tracks, daemon=True).start()


def pick_match(key: str, pick: int) -> dict:
    with lock:
        if key in matches:
            matches[key]["pick"] = pick
    save_matches()
    return {"ok": True}


def match_for(key: str) -> dict:
    """Совпадение для одного трека (если фон до него ещё не дошёл) — ищем сразу и запоминаем."""
    if key not in matches:
        t = next((t for t in tracks_to_match() if t["key"] == key), None)
        if t:
            match_one(t)
            save_matches()
    return matches.get(key, {"candidates": [], "pick": 0})


_songs = None


def lib_songs() -> list[dict]:
    global _songs
    if _songs is None:
        _songs = [{"id": s["id"], "artist": build.fix_mojibake(s.get("displayArtist") or s.get("artist") or ""),
                   "title": build.fix_mojibake(s.get("title")), "album": build.fix_mojibake(s.get("album")),
                   "tkey": build.title_key(build.fix_mojibake(s.get("title")))}
                  for s in build.load("navidrome/songs.json")]
    return _songs


def lib_track_candidates(artist: str, title: str) -> dict:
    """Кандидаты для «уже есть»: треки похожего артиста (по похожести названия) +
    треки с тем же названием у других артистов."""
    from difflib import SequenceMatcher
    tk = build.title_key(title)
    own, other = [], []
    similar = {}  # сравнение артистов дорогое — по одному разу на имя, а не на каждый трек
    for s in lib_songs():
        if s["artist"] not in similar:
            similar[s["artist"]] = build.artists_similar(artist, s["artist"])
        same_artist = similar[s["artist"]]
        if not same_artist and (not tk or s["tkey"][:1] != tk[:1]):
            continue  # чужой артист и другое начало названия — точно не кандидат
        ratio = SequenceMatcher(None, tk, s["tkey"]).ratio() if tk else 0
        if same_artist:
            own.append((ratio, s))
        elif ratio >= 0.85:
            other.append((ratio, s))
    own.sort(key=lambda x: -x[0])
    other.sort(key=lambda x: -x[0])
    pack = lambda xs, n: [{**{k: v for k, v in s.items() if k != "tkey"}, "score": round(r, 2)} for r, s in xs[:n]]
    return {"artist_tracks": pack(own, 60), "same_title": pack(other, 10), "artist_total": len(own)}


SNAPSHOT = ROOT / "raw" / "navidrome" / "songs.json"


def snapshot_info() -> dict:
    ts = SNAPSHOT.stat().st_mtime if SNAPSHOT.exists() else 0
    return {"ts": int(ts), "text": time.strftime("%d.%m %H:%M", time.localtime(ts)) if ts else "нет"}


def refresh_library() -> dict:
    """Свежий снимок Navidrome + пересборка out/. Прежний снимок кладём в raw/navidrome/prev/."""
    global _library, _lib, _events, _songs
    import shutil
    import navidrome_dump
    src = SNAPSHOT.parent
    prev = src / "prev"
    prev.mkdir(exist_ok=True)
    for f in src.glob("*.json"):
        shutil.copy2(f, prev / f.name)
    navidrome_dump.main()
    build.build()
    _library = _lib = _events = _songs = None
    globals()["_songs_by_title"] = None
    start_matching()  # новые треки после пересборки — досопоставить
    event("library_refresh", songs=len(read_json(SNAPSHOT, [])))
    return snapshot_info()


def release_title(label: str) -> str:
    """'2015 Title · альбом' / '2015 Title (EP)' → 'title'."""
    label = re.sub(r"\s+·\s+[^·]+$|\s+\(EP\)$", "", label or "")
    return build.words(re.sub(r"^\d{4}\s+", "", label))


def pending_plan(types: set[str], top_up: bool = False) -> list[dict]:
    decisions = read_json(DECISIONS, {})
    sent = read_json(DOWNLOADS, {})
    plan, by_artist = [], {}
    # всё, что уже уходило в deemix по артисту (с любого решения) — чтобы не предлагать повторно
    sent_by_artist: dict[str, list] = {}
    sent_meta: dict[str, list] = {}  # артист → отправки: когда и что тогда было исключено
    for k, s in sent.items():
        d = decisions.get(k, {})
        aid = d.get("artist_id") or (d.get("deezer_id") if d.get("kind") == "artists" else None)
        link = s.get("artist_link") or (f"https://www.deezer.com/artist/{aid}" if aid else None)
        if link:
            sent_by_artist.setdefault(link, []).extend(s["urls"])
            if not s.get("dismissed"):
                sent_meta.setdefault(link, []).append(s)
    for k, d in decisions.items():
        if d.get("decision") not in DOWNLOADABLE:
            continue
        s = sent.get(k)
        if s and (s.get("dismissed") or not (top_up and d["decision"] in ("discography", "label"))):
            continue  # отправлено/убрано — из плана уходит; дозагрузка только по запросу
        if d["decision"] == "discography" and not top_up:
            aid = d.get("artist_id") or (d.get("deezer_id") if d.get("kind") == "artists" else None)
            if aid and f"https://www.deezer.com/artist/{aid}" in sent_by_artist:
                continue  # этого артиста уже отправляли по другому решению
        item = plan_item(k, d, types)
        item["keys"] = [k]
        if d["decision"] == "discography" and item["artist_link"]:
            if item["artist_link"] in by_artist:  # два трека одного артиста → одна дискография
                other = by_artist[item["artist_link"]]
                other["keys"].append(k)
                other["name"] += f" · {d.get('name')}"
                continue
            by_artist[item["artist_link"]] = item
            done_urls = sent_by_artist.get(item["artist_link"], [])
            if done_urls:
                done = {u["url"] for u in done_urls}
                # у одного альбома на Deezer бывает несколько ID — сверяем ещё и по названию
                done_titles = {release_title(u["label"]) for u in done_urls}
                for r in item["releases"]:
                    if r["url"] in done or release_title(r["label"]) in done_titles:
                        r["sent"], r["checked"] = True, False
                exclude_unsent(item, sent_meta.get(item["artist_link"], []))
                item["urls"] = [{"url": r["url"], "label": r["label"]} for r in item["releases"] if r["checked"]]
                item["top_up"] = len(done)
                if not item["urls"]:
                    continue  # в дозагрузке показываем только тех, у кого есть что докачать
        elif d["decision"] == "label" and s:  # дозагрузка лейбла: отправленное — серым
            done = {u["url"] for u in s["urls"]}
            for r in item["releases"]:
                if r["url"] in done:
                    r["sent"], r["checked"] = True, False
            # старые отправки лейблов (без списка excluded): галки там только добавлялись, а не
            # отправленное — это не найденное тогда (поиск с тех пор улучшен) или сборники, не прошедшие
            # фильтр типов. Не исключаем ничего; новые отправки исключённое помнят сами
            exclude_unsent(item, [s], legacy_keep=lambda r: True)
            # уже лежит на диске, но снимок библиотеки ещё старый — не качать повторно
            try:
                keys = disk_index_cached()
            except RuntimeError:
                keys = set()
            for r in item["releases"]:
                if r.get("checked") and keys and on_disk(keys, r["label"], r["url"]):
                    r["checked"], r["in_deemix"] = False, "уже на диске"
            item["urls"] = [{"url": r["url"], "label": r["label"]} for r in item["releases"] if r["checked"]]
            item["top_up"] = len(done)
            if not item["urls"]:
                continue
        plan.append(item)
    apply_checks(plan)
    mark_in_deemix(plan)  # после ручных галочек: уже лежащее в deemix не шлём никогда
    mark_in_plan(plan, decisions)
    return plan


def plan_response(types: set[str], top_up: bool) -> dict:
    plan = pending_plan(types, top_up)
    add_durations(plan)
    return {"plan": plan, "rates": size_rates()}


def mark_in_plan(plan: list[dict], decisions: dict) -> None:
    """Один альбом Deezer — в одной задаче: Дубышкин отдельной дискографией и он же в каталоге
    лейбла Trip. Отмеченный релиз остаётся за задачей, созданной раньше; в остальных —
    пометка «есть в плане» и снятая галка."""
    owner: dict[str, str] = {}
    for item in sorted(plan, key=lambda p: decisions.get(p["key"], {}).get("ts", 0)):
        for r in item["releases"]:
            if r.get("checked"):
                owner.setdefault(r["url"], item["key"])
    names = {p["key"]: p["name"] for p in plan}
    for item in plan:
        for r in item["releases"]:
            other = owner.get(r["url"])
            if other and other != item["key"]:
                r["in_plan"] = names[other]
                r["checked"] = False
        item["urls"] = [{"url": r["url"], "label": r["label"]} for r in item["releases"] if r["checked"]]


_rates = None


def size_rates() -> dict:
    """Байт в секунду звука — по своей же библиотеке (снимок Navidrome): FLAC электроники жмётся
    не так, как классика, поэтому не формула, а среднее по реальным файлам."""
    global _rates
    if _rates is None:
        songs = read_json(SNAPSHOT, [])
        out = {}
        for fmt, ok in (("flac", lambda x: x.get("suffix") == "flac"),
                        ("mp3", lambda x: x.get("suffix") == "mp3" and (x.get("bitRate") or 0) >= 315)):
            xs = [x for x in songs if ok(x) and x.get("size") and x.get("duration")]
            dur = sum(x["duration"] for x in xs)
            out[fmt] = round(sum(x["size"] for x in xs) / dur) if dur else {"flac": 110000, "mp3": 40000}[fmt]
        _rates = out
    return _rates


def prefetch_albums(urls: list[str]) -> None:
    """Карточки Deezer для многих релизов разом — параллельно (лимит Deezer ~10 запросов/с,
    по одному выходит ~1 запрос/с: у Eminem 345 релизов — минуты вместо секунд)."""
    from concurrent.futures import ThreadPoolExecutor
    todo = [u for u in urls if f"https://api.deezer.com/{'/'.join(u.rstrip('/').split('/')[-2:])}?" not in cache]
    if todo:
        with ThreadPoolExecutor(8) as ex:
            list(ex.map(release_duration, todo))


def release_duration(url: str) -> tuple[int | None, int | None]:
    """Длительность (с) и число треков релиза по карточке Deezer (кэш Deezer бессрочный)."""
    kind, _, rid = url.rstrip("/").rpartition("/")
    if kind.endswith("/track"):
        return deezer(f"track/{rid}").get("duration"), 1
    a = deezer(f"album/{rid}")
    return a.get("duration"), a.get("nb_tracks")


def add_durations(plan: list[dict]) -> None:
    prefetch_albums([r["url"] for item in plan for r in item["releases"] if "duration" not in r])
    for item in plan:
        for r in item["releases"]:
            if "duration" not in r:
                r["duration"], r["nb_tracks"] = release_duration(r["url"])
    flush_cache()


def discography_sizes(artist_id: int) -> dict:
    """Длительность релизов дискографии — отдельным запросом после самой дискографии: у большого
    артиста это сотни карточек Deezer (первый раз — секунды, потом из кэша)."""
    out = {}
    rels = discography(artist_id)
    prefetch_albums([r["url"] for r in rels])
    for r in rels:
        dur, n = release_duration(r["url"])
        out[str(r["id"])] = {"duration": dur, "nb_tracks": n}
    flush_cache()
    return {"releases": out, "rates": size_rates()}


def plan_artists() -> dict:
    """Deezer-артисты, по которым есть задача на загрузку: id → {name, status: plan | sent}.
    Без расчёта плана — только решения и отправленное, чтобы метки в поиске были мгновенными."""
    decisions, sent = read_json(DECISIONS, {}), read_json(DOWNLOADS, {})
    out = {}
    for k, d in decisions.items():
        if d.get("decision") not in DOWNLOADABLE:
            continue
        aid = d.get("artist_id") or (d.get("deezer_id") if d.get("kind") == "artists" else None)
        if not aid:
            continue
        s_ = sent.get(k)
        if s_ and s_.get("dismissed"):
            continue
        status = "sent" if s_ else "plan"
        prev = out.get(str(aid))
        if not prev or prev["status"] == "sent":  # «в плане» важнее «отправлено»
            out[str(aid)] = {"name": d.get("name"), "status": status, "decision": d["decision"]}
    return out


def planned_urls(types: set[str]) -> dict[str, str]:
    """URL релизов, которые план отправит (отмечены) → название задачи. Для вкладки «Лейблы»."""
    return {r["url"]: p["name"] for p in pending_plan(types) for r in p["releases"] if r.get("checked")}


def exclude_unsent(item: dict, sends: list[dict], legacy_keep=lambda r: False) -> None:
    """Дозагрузка: не отправленное тогда — не отмечать снова. Что было исключено, запоминается при
    отправке («excluded»); для старых отправок без этого списка — всё, что вышло до отправки,
    считается исключённым (кроме legacy_keep: у лейблов сборники не уходили из-за фильтра типов)."""
    if not sends:
        return
    excluded = {u for s in sends for u in s.get("excluded", [])}
    legacy = any("excluded" not in s for s in sends)
    last = time.strftime("%Y-%m-%d", time.localtime(max(s["ts"] for s in sends)))
    for r in item["releases"]:
        if r.get("sent") or not r.get("checked"):
            continue
        old = (r.get("date") or "9999") <= last
        if r["url"] in excluded or (legacy and old and not legacy_keep(r)):
            r["checked"], r["excluded"] = False, True


def url_qid(url: str) -> str:
    """'https://www.deezer.com/album/123' → 'album_123' (начало uuid элемента очереди deemix)."""
    return "_".join(url.rstrip("/").split("/")[-2:])


def deemix_queue_ids() -> dict[str, str]:
    """Что уже есть в очереди deemix: 'album_123' → статус. deemix падает, если добавить элемент,
    который у него уже лежит скачанным (uuid встаёт в очередь, а файл без треков), — такое не шлём."""
    try:
        q = deemix.queue().get("queue", {})
    except Exception:
        return {}
    return {u.rsplit("_", 1)[0]: (it or {}).get("status", "") for u, it in q.items()}


def mark_in_deemix(plan: list[dict]) -> None:
    inq = deemix_queue_ids()
    for item in plan:
        for r in item["releases"]:
            st = inq.get(url_qid(r["url"]))
            if st:
                r["in_deemix"] = "скачано" if st == "completed" else "в очереди deemix"
                r["checked"] = False
        item["urls"] = [{"url": r["url"], "label": r["label"]} for r in item["releases"] if r["checked"]]


PLAN_STATE = ROOT / "plan_state.json"  # ручные галочки в плане: ключ решения → {url: bool}


def apply_checks(plan: list[dict]) -> None:
    state = read_json(PLAN_STATE, {})
    for item in plan:
        marks = state.get(item["key"], {})
        if not marks:
            continue
        for r in item["releases"]:
            if r["url"] in marks and not r.get("have"):  # уже есть в библиотеке — галка не нужна
                r["checked"] = marks[r["url"]]
        item["urls"] = [{"url": r["url"], "label": r["label"]} for r in item["releases"] if r["checked"]]


def set_checks(key: str, marks: dict[str, bool]) -> dict:
    with lock:
        state = read_json(PLAN_STATE, {})
        state.setdefault(key, {}).update(marks)
        write_json(PLAN_STATE, state)
    event("plan_checks", key=key, on=sum(1 for v in marks.values() if v), off=sum(1 for v in marks.values() if not v))
    return {"ok": True}


def clear_checks(keys: list[str]) -> None:
    with lock:
        state = read_json(PLAN_STATE, {})
        for k in keys:
            state.pop(k, None)
        write_json(PLAN_STATE, state)


def dismiss(keys: list[str]) -> dict:
    """Убрать решения из плана, ничего не отправляя."""
    with lock:
        sent = read_json(DOWNLOADS, {})
        decisions = read_json(DECISIONS, {})
        for k in keys:
            sent.setdefault(k, {"ts": int(time.time()), "name": decisions.get(k, {}).get("name"),
                                "urls": [], "uuids": []})["dismissed"] = True
        write_json(DOWNLOADS, sent)
    clear_checks(keys)
    event("dismiss", keys=keys, names=[decisions.get(k, {}).get("name") for k in keys])
    return {"ok": True}


# ---------- deemix ----------

class Deemix:
    """Клиент локального deemix-gui. Очередь у deemix общая, но вход — на HTTP-сессию,
    поэтому логинимся своей сессией: ARL из config.json ("deemix_arl"), либо тот, что
    deemix сохранил сам (login.json, режим single user)."""

    def __init__(self):
        self.s = requests.Session()
        self.logged = False

    def login(self) -> None:
        if self.logged:
            return
        info = self.s.get(f"{DEEMIX}/api/connect", timeout=15).json()
        if not info.get("autologin"):
            self.logged = True
            return
        cfg = read_json(ROOT / "config.json", {})
        arl = cfg.get("deemix_arl") or (info.get("singleUser") or {}).get("arl")
        if not arl:
            raise RuntimeError('нет ARL для deemix — добавь "deemix_arl" в config.json')
        r = self.s.post(f"{DEEMIX}/api/loginArl", json={"arl": arl}, timeout=30).json()
        if r.get("status") not in (1, 2, 3):
            raise RuntimeError(f"deemix login status {r.get('status')}")
        self.logged = True

    def add(self, urls: list[str], bitrate: int | None) -> dict:
        self.login()
        urls = [deemix_url(u) for u in urls]
        r = self.s.post(f"{DEEMIX}/api/addToQueue",
                        json={"url": " ".join(urls), "bitrate": bitrate}, timeout=300).json()
        if not r.get("result") and r.get("errid") == "NotLoggedIn":
            self.logged = False
            self.login()
            r = self.s.post(f"{DEEMIX}/api/addToQueue",
                            json={"url": " ".join(urls), "bitrate": bitrate}, timeout=300).json()
        return r

    def queue(self) -> dict:
        return self.s.get(f"{DEEMIX}/api/getQueue", timeout=15).json()

    def post(self, endpoint: str, body: dict) -> dict:
        self.login()
        return self.s.post(f"{DEEMIX}/api/{endpoint}", json=body, timeout=30).json()


deemix = Deemix()


def deemix_url(url: str) -> str:
    """Альбом из одного трека deemix (0.4.5 и 0.5.0) сам превращает в трек и падает на проверке
    схемы (нет title_version) — молча, ничего не добавив в очередь. Ссылку на сам трек он берёт."""
    m = re.search(r"/album/(\d+)", url)
    if not m:
        return url
    tracks = (deezer(f"album/{m.group(1)}").get("tracks") or {}).get("data") or []
    return f"https://www.deezer.com/track/{tracks[0]['id']}" if len(tracks) == 1 else url


def queue_uuid(url: str, bitrate) -> str:
    """uuid элемента очереди deemix = <type>_<id>_<bitrate>."""
    return "_".join(deemix_url(url).rstrip("/").split("/")[-2:]) + f"_{bitrate}"


def send_to_deemix(selection: dict[str, list[str]], bitrate: int | None, types: set[str],
                   top_up: bool = False) -> dict:
    """selection: ключ решения → отмеченные на странице URL релизов."""
    plan = {p["key"]: p for p in pending_plan(types, top_up)}
    results = []
    event("send_start", tasks=len(selection), releases=sum(len(v) for v in selection.values()),
          bitrate=bitrate, top_up=top_up)
    for k, picked in selection.items():
        p = plan.get(k)
        if not p:
            event("send_skipped", key=k, reason="задачи нет в плане (уже отправлена или убрана)")
            continue
        # «есть в плане» — релиз уходит со своей (более ранней) задачей, отсюда не шлём
        known = {r["url"]: r["label"] for r in p["releases"] if not r.get("in_plan")}
        inq = deemix_queue_ids()
        # только из плана, без повторов и без того, что уже лежит в deemix (иначе deemix падает)
        urls = [{"url": u, "label": known[u]} for u in dict.fromkeys(picked) if u in known and url_qid(u) not in inq]
        if not urls:
            event("send_skipped", key=k, task=p["name"], reason="всё отмеченное уже в deemix или в другой задаче")
            continue
        # частями: 200 релизов deemix принимает дольше любого таймаута, а запрос, оборванный по
        # таймауту, deemix всё равно выполняет — без записи у нас. Каждая часть пишется сразу.
        ok, err = True, None
        for i in range(0, len(urls), SEND_CHUNK):
            part = urls[i:i + SEND_CHUNK]
            t0 = time.time()
            try:
                r = deemix.add([u["url"] for u in part], bitrate)
            except requests.RequestException as exc:
                ok, err = False, f"deemix не ответил: {exc.__class__.__name__}"
                event("send_failed", key=k, task=p["name"], part=i // SEND_CHUNK + 1, error=err,
                      sec=round(time.time() - t0), not_sent=[u["label"] for u in urls[i:]])
                break
            if not r.get("result"):
                ok, err = False, r.get("errid")
                event("send_failed", key=k, task=p["name"], part=i // SEND_CHUNK + 1, error=err,
                      not_sent=[u["label"] for u in urls[i:]])
                break
            event("send_chunk", key=k, task=p["name"], part=i // SEND_CHUNK + 1, sec=round(time.time() - t0),
                  releases=[u["label"] for u in part], urls=[u["url"] for u in part])
            # uuid элемента очереди deemix = <type>_<id>_<bitrate>, считаем сами из URL
            eff = (r.get("data") or {}).get("bitrate")
            record_sent(k, p, part, [queue_uuid(u["url"], eff) for u in part],
                        bitrate, set(picked))
        if ok:
            clear_checks(p["keys"])
            event("send_task_done", key=k, task=p["name"], releases=len(urls))
        results.append({"key": k, "ok": ok, "error": err})
    event("send_done", ok=sum(r["ok"] for r in results), failed=[r["key"] for r in results if not r["ok"]])
    return {"results": results}


SEND_CHUNK = 20


def record_sent(k: str, p: dict, urls: list[dict], uuids: list[str], bitrate, picked: set[str]) -> None:
    """Дописать в журнал отправленное. excluded — что было в задаче, но не выбрано: в дозагрузке
    оно не отмечается само (раньше вернувшиеся «исключённые» синглы Sipe)."""
    with lock:
        sent = read_json(DOWNLOADS, {})
        prev = sent.get(k, {"urls": [], "uuids": []})
        chosen = picked | {u["url"] for u in prev["urls"]}
        # исключённое — только то, что можно было отметить, но не отметили; не отмеченное самой
        # программой (есть в библиотеке/на диске/в deemix/в другой задаче) — не выбор человека
        auto = {r["url"] for r in p["releases"]
                if r.get("have") or r.get("sent") or r.get("in_deemix") or r.get("in_plan")}
        excluded = sorted(set(prev.get("excluded", [])) | {r["url"] for r in p["releases"]} - chosen - auto)
        sent[k] = {"ts": int(time.time()), "name": p["name"], "urls": prev["urls"] + urls,
                   "uuids": prev["uuids"] + uuids, "bitrate": bitrate, "artist_link": p.get("artist_link"),
                   "excluded": excluded}
        for other in p["keys"][1:]:  # склеенные решения того же артиста — тоже закрыты
            sent.setdefault(other, {"ts": int(time.time()), "name": p["name"], "urls": [],
                                    "uuids": [], "artist_link": p.get("artist_link"), "merged_into": k})
        write_json(DOWNLOADS, sent)


# ---------- сверка отправленного с диском и повтор ----------
# После ребута/отвала диска deemix помечает релизы failed, а застрявшие inQueue сам не продолжает.
# Очередь deemix — не источник правды (скачанное оттуда убирают), поэтому сверяем с диском.

DEEMIX_DIR = Path.home() / "AppData" / "Roaming" / "deemix"


def disk_index() -> set:
    """Что лежит в папке загрузок deemix: альбомы — папками «Артист - Альбом», синглы — файлами
    «Артист - Трек.flac» прямо в папке артиста (createSingleFolder: false). '/' deemix пишет как '_'."""
    cfg = read_json(DEEMIX_DIR / "config.json", {})
    root = Path(cfg.get("downloadLocation") or r"H:\data\media\music\Deezer")
    if not root.exists():
        raise RuntimeError(f"папка загрузок недоступна: {root} — сетевой диск не подключён?")
    audio = (".flac", ".mp3")
    keys = set()

    def add(name: str) -> None:
        keys.update(discogs.title_keys(name.split(" - ", 1)[-1].replace("_", " ")))

    def has_audio(path: str, depth: int = 0) -> bool:
        # многодисковые альбомы deemix кладёт в подпапки CD1/CD2 (createCDFolder)
        for f in os.scandir(path):
            if f.name.lower().endswith(audio) or (depth == 0 and f.is_dir() and has_audio(f.path, 1)):
                return True
        return False
    for a in os.scandir(root):
        if not a.is_dir():
            continue
        for x in os.scandir(a.path):
            if x.is_dir():
                if has_audio(x.path):
                    add(x.name)
            elif x.name.lower().endswith(audio):
                add(os.path.splitext(x.name)[0])
    return keys


_disk_cache: list = [0.0, None]


def disk_index_cached() -> set:
    """disk_index раз в минуту: обход 3000+ папок на сетевом диске — секунды."""
    if time.time() - _disk_cache[0] > 60 or _disk_cache[1] is None:
        _disk_cache[1] = disk_index()
        _disk_cache[0] = time.time()
    return _disk_cache[1]


def sent_title(label: str) -> str:
    """'2024 Liuos — Vastness EP · EP' → 'Vastness EP' (у задач лейбла в названии ещё и артист)."""
    t = re.sub(r"^\d{4}\s+", "", re.sub(r"\s+·\s+[^·]+$", "", label or ""))
    return t.split(" — ", 1)[-1]


def on_disk(keys: set, label: str, url: str) -> bool:
    """Есть ли отправленный релиз на диске: по нашему названию, а если нет — по названию на Deezer
    (папку deemix называет по Deezer, а у задач лейбла в записи название с Beatport)."""
    if discogs.title_keys(sent_title(label).replace("/", " ").replace("_", " ")) & keys:
        return True
    m = re.search(r"/album/(\d+)", url)
    if not m:
        return False
    card = deezer(f"album/{m.group(1)}")
    t = card.get("title")
    if t and discogs.title_keys(t.replace("/", " ").replace("_", " ")) & keys:
        return True
    # синглы deemix кладёт файлами по названию трека: 'Twilight' → 'Twilight (John Monkman Remix).flac'
    tracks = [x["title"] for x in (card.get("tracks") or {}).get("data") or []]
    return bool(tracks) and all(discogs.title_keys(x.replace("/", " ").replace("_", " ")) & keys for x in tracks)


def audit_downloads() -> dict:
    keys = disk_index()
    files = {}
    for f in (DEEMIX_DIR / "queue").glob("*.json"):
        if f.name != "order.json":
            files[f.stem] = read_json(f, {})
    order = set(read_json(DEEMIX_DIR / "queue" / "order.json", []))
    out = {"on_disk": 0, "failed": [], "stuck": [], "missing": [], "queued": 0}
    for k, v in read_json(DOWNLOADS, {}).items():
        if v.get("dismissed"):
            continue
        for uuid, u in zip(v.get("uuids", []), v.get("urls", [])):
            item = files.get(uuid)
            if item is None and uuid.startswith("album_") and not on_disk(keys, u["label"], u["url"]):
                # сингл из одного трека уходит в deemix ссылкой на трек — и в очереди он под track_…
                item = files.get(queue_uuid(u["url"], uuid.rsplit("_", 1)[-1]))
            item = item or {}
            st = item.get("status")
            row = {"uuid": uuid, "task": v.get("name"), "label": u["label"], "url": u["url"]}
            if st in ("failed", "withErrors"):
                out["failed"].append(row | {"error": ((item.get("errors") or [{}])[0].get("message") or "")[:200]})
            elif st in ("inQueue", "downloading") and uuid not in order:
                out["stuck"].append(row)  # лежит в очереди, но deemix его не продолжит (нет в order.json)
            elif st in ("inQueue", "downloading"):
                out["queued"] += 1
            elif on_disk(keys, u["label"], u["url"]):
                out["on_disk"] += 1
            else:
                out["missing"].append(row)
    return out


def audit_logged() -> dict:
    a = audit_downloads()
    event("audit", on_disk=a["on_disk"], queued=a["queued"], failed=len(a["failed"]),
          stuck=len(a["stuck"]), missing=len(a["missing"]))
    return a


def retry_downloads() -> dict:
    """Упавшее, застрявшее и не найденное на диске — заново в deemix его же повтором
    (/api/retryDownload: uuid → url + битрейт, запись в очереди перезаписывается).
    Уже скачанные файлы deemix не перекачивает (overwriteFile), так что лишний повтор безвреден."""
    a = audit_downloads()
    rows = a["failed"] + a["stuck"] + a["missing"]
    ok, errors = 0, []
    deemix.login()
    queued = {f.stem for f in (DEEMIX_DIR / "queue").glob("*.json")}
    for r in rows:
        # записи в очереди нет (очищена, deemix переустановлен) — повтор не сработает, добавляем заново;
        # uuid = <type>_<id>_<bitrate>, так что он совпадёт с записанным у нас
        res = deemix.post("retryDownload", {"uuid": r["uuid"]}) if r["uuid"] in queued else \
            deemix.add([r["url"]], int(r["uuid"].rsplit("_", 1)[-1]))
        if res.get("result"):
            ok += 1
        else:
            errors.append(f"{r['label']}: {res.get('errid')}")
    event("retry", retried=ok, releases=[f"{r['task']} · {r['label']}" for r in rows])
    if errors:
        event("retry_failed", errors=errors)
    return {"retried": ok, "errors": errors}


# ---------- наблюдение за очередью deemix ----------
# Раз в минуту: смена статусов релизов (упал — с текстом ошибки), deemix пропал/вернулся,
# зависшая очередь. Плюс ежедневная копия состояния. То, что 01.10 пришлось выяснять задним числом.

QUEUE_STATE = journal.LOG_DIR / "queue_state.json"
WATCH_EVERY = 60


def uuid_tasks() -> dict[str, str]:
    """uuid очереди deemix → наша задача (по журналу отправок)."""
    out = {}
    for v in read_json(DOWNLOADS, {}).values():
        for u in v.get("uuids", []):
            out[u] = v.get("name")
    return out


def watch_queue() -> None:
    state = read_json(QUEUE_STATE, {})
    reachable, idle, stalled_logged, stuck_logged = None, 0, False, 0
    while True:
        try:
            journal.daily_backup()
            try:
                q = deemix.queue()
                up = True
            except requests.RequestException:
                q, up = {}, False
            if up != reachable:
                if reachable is not None or not up:
                    event("deemix_up" if up else "deemix_down")
                reachable = up
            if up:
                items, order = q.get("queue", {}), set(q.get("queueOrder", []))
                tasks = uuid_tasks()
                new = {}
                for u, it in items.items():
                    st = (it or {}).get("status")
                    new[u] = st
                    if state.get(u) == st:
                        continue
                    rec = {"uuid": u, "release": f"{it.get('artist')} — {it.get('title')}", "task": tasks.get(u),
                           "from": state.get(u), "to": st}
                    if st in ("failed", "withErrors"):
                        msgs = list(dict.fromkeys((e.get("message") or "")[:200] for e in it.get("errors") or []))
                        event("deemix_failed", **rec, failed=it.get("failed"), size=it.get("size"), errors=msgs[:3])
                    elif st == "completed":
                        event("deemix_completed", **rec, size=it.get("size"))
                state = new
                write_json(QUEUE_STATE, state)
                # лежит inQueue, но нет в order — deemix его сам не продолжит (так было после ребута)
                stuck = [u for u, st in new.items() if st == "inQueue" and u not in order]
                if len(stuck) != stuck_logged:
                    if stuck:
                        event("deemix_stuck", count=len(stuck), examples=[items[u].get("title") for u in stuck[:5]])
                    stuck_logged = len(stuck)
                # есть что качать, но ничего не качается уже 5 проверок подряд
                waiting = any(st == "inQueue" for st in new.values())
                busy = any(st == "downloading" for st in new.values())
                idle = idle + 1 if waiting and not busy else 0
                if idle >= 5 and not stalled_logged:
                    event("deemix_stalled", waiting=sum(1 for st in new.values() if st == "inQueue"),
                          minutes=idle * WATCH_EVERY // 60)
                    stalled_logged = True
                if idle == 0:
                    stalled_logged = False
        except Exception:
            journal.log.exception("watch_queue")
        time.sleep(WATCH_EVERY)


# ---------- Soulseek: то, чего нет на Deezer ----------
# Очередь копится сама: релизы лейблов из плана, не найденные на Deezer, и треки, которые deemix
# не скачал («not available on deezer's servers»). Поиск — фоном, своими потоками и своей блокировкой:
# поиск в Soulseek идёт десятки секунд на запрос и ничего в плане/интерфейсе не держит.

SLSK_QUEUE = ROOT / "slsk_queue.json"
SLSK_SYNC_EVERY = 600       # с: пересобрать очередь из источников
SLSK_RETRY_NONE = 86400     # не найденное — искать снова через сутки
SLSK_RETRY_PARTIAL = 3 * 86400
slsk_lock = threading.Lock()
slsk_items: dict = read_json(SLSK_QUEUE, {})
slsk_state = {"searching": [], "last_sync": 0, "error": None}
for _it in slsk_items.values():  # поиск, оборванный перезапуском, — заново
    if _it.get("status") == "searching":
        _it["status"] = "queued"
    if _it.get("status") in ("downloading", "download_requested"):  # уже идущее — считаем отправленным
        _it["want"] = True
    # найдено до того, как треки релиза стали храниться по порядку и с тегами, — пересчитать (поиск из кэша)
    if _it.get("mode") == "release" and _it.get("status") in ("found", "partial") and             not all("position" in t for t in _it.get("tracks") or [{}]):
        _it["status"] = "queued"


def slsk_save() -> None:
    with slsk_lock:
        write_json(SLSK_QUEUE, slsk_items)


def slsk_add(item: dict) -> bool:
    """Новое — в очередь; известное не трогаем (и убранное руками назад не возвращаем)."""
    with slsk_lock:
        if item["id"] in slsk_items:
            return False
        slsk_items[item["id"]] = item | {"status": "queued", "added": int(time.time())}
        return True


def slsk_sources() -> int:
    """Собрать очередь из источников. Ничего не ищет и не ждёт: каталог лейбла берётся только готовый."""
    added = 0
    decisions = read_json(DECISIONS, {})
    labels = read_json(LABELS, {})
    for key, d in decisions.items():
        if d.get("decision") != "label" or not d.get("label_id"):
            continue
        lab = (labels.get(f"bp:{d['label_id']}") or {}).get("data")
        if not lab:
            continue
        src = {"kind": "label", "key": key, "name": d.get("name") or lab.get("name")}
        for r in lab.get("releases", []):
            if r.get("deezer") or r.get("have"):
                continue
            rid = r["src"].rstrip("/").rsplit("/", 1)[-1]
            added += slsk_add({"id": f"bp:{rid}", "mode": "release", "source": src, "artist": r["artist"],
                               "title": r["title"], "year": r.get("year"), "catno": (r.get("catno") or "").strip(),
                               "type": r.get("type"), "link": r["src"]})
    # треки, которые deemix не смог скачать: у ошибки есть id трека Deezer
    try:
        q = deemix.queue().get("queue", {})
    except requests.RequestException:
        q = {}
    tasks = uuid_tasks()
    for u, it in q.items():
        if (it or {}).get("status") not in ("failed", "withErrors"):
            continue
        for e in it.get("errors") or []:
            data = e.get("data") or {}
            if not data.get("id") or "not available" not in (e.get("message") or ""):
                continue
            name = tasks.get(u) or "deemix"
            added += slsk_add({"id": f"dz:{data['id']}", "mode": "track",
                               "source": {"kind": "deemix", "key": f"deemix:{name}", "name": name},
                               "artist": data.get("artist") or it.get("artist"), "title": data.get("title"),
                               "album": it.get("title"), "link": f"https://www.deezer.com/track/{data['id']}"})
    slsk_state["last_sync"] = int(time.time())
    if added:
        slsk_save()
        event("slsk_queued", added=added, total=len(slsk_items))
    return added


def slsk_tracks(it: dict) -> list[dict]:
    """Ожидаемые треки с длительностями: у релиза лейбла — с Beatport, у трека — с Deezer."""
    if it["mode"] == "track":
        t = deezer(f"track/{it['id'][3:]}")
        it["artist"] = (t.get("artist") or {}).get("name") or it.get("artist")
        it["artists"] = [c["name"] for c in t.get("contributors") or []] or [it["artist"]]
        return [{"title": t.get("title") or it["title"], "duration": t.get("duration")}]
    rid = it["id"][3:]
    rel = beatport.get(f"/catalog/releases/{rid}/")
    ts = beatport.get(f"/catalog/releases/{rid}/tracks/", per_page=100).get("results", [])
    order = {int(u.rstrip("/").rsplit("/", 1)[-1]): i for i, u in enumerate(rel.get("tracks") or [])}
    ts.sort(key=lambda x: order.get(x["id"], 999))
    return [{"title": f"{x['name']} ({x['mix_name']})" if x.get("mix_name") else x["name"],
             "duration": round(x["length_ms"] / 1000) if x.get("length_ms") else None,
             "artists": [a["name"] for a in x.get("artists", [])], "position": i + 1, "isrc": x.get("isrc"),
             "bpm": x.get("bpm"), "genre": (x.get("genre") or {}).get("name"), "id": x["id"]} for i, x in enumerate(ts)]


def slsk_due(it: dict, now: float) -> bool:
    st = it.get("status")
    if st == "queued":
        return True
    if st in ("none", "error"):
        return now - it.get("searched", 0) > (6 * 3600 if it.get("want") else SLSK_RETRY_NONE)
    if st == "partial":
        return now - it.get("searched", 0) > SLSK_RETRY_PARTIAL
    if st == "dl_failed":  # ни у кого не скачалось — через сутки ищем заново (пиры другие)
        return now - it.get("dl", {}).get("ended", 0) > SLSK_RETRY_NONE
    return False


def slsk_take() -> dict | None:
    now = time.time()
    with slsk_lock:
        # сначала новые, потом повторы; внутри — по порядку добавления
        due = sorted((it for it in slsk_items.values() if slsk_due(it, now)),
                     key=lambda it: (it.get("status") != "queued", it.get("added", 0)))
        if not due:
            return None
        it = due[0]
        it["status"] = "searching"
        return it


def slsk_search_one(it: dict) -> None:
    slsk_state["searching"].append(it["id"])
    try:
        tracks = slsk_tracks(it)
        if not tracks:
            raise RuntimeError("нет списка треков")
        if it["mode"] == "track":
            r = slsk.find_track(it["artist"], tracks[0]["title"], tracks[0]["duration"], it.get("artists"))
        else:
            r = slsk.find_release(it["artist"], it["title"], tracks, it.get("catno"))
        with slsk_lock:
            it.update(status=r["status"], result=r, tracks=tracks, searched=int(time.time()), error=None)
            if it.get("want") and r.get("best"):  # отправлено руками и нашлось — качать дальше
                it["status"] = "download_requested"
    except Exception as exc:  # noqa: BLE001 — одна ошибка не должна останавливать очередь
        journal.log.exception("slsk %s", it["id"])
        with slsk_lock:
            it.update(status="error", error=repr(exc)[:300], searched=int(time.time()))
    finally:
        slsk_state["searching"].remove(it["id"])
        slsk_save()


def slsk_worker() -> None:
    while True:
        try:
            it = slsk_take()
            if it:
                slsk_search_one(it)
                continue
        except Exception:  # noqa: BLE001
            journal.log.exception("slsk_worker")
        time.sleep(30)


def slsk_syncer() -> None:
    while True:
        try:
            slsk_sources()
            slsk_state["error"] = None
        except Exception as exc:  # noqa: BLE001
            slsk_state["error"] = repr(exc)[:300]
            journal.log.exception("slsk_sources")
        time.sleep(SLSK_SYNC_EVERY)


# ---------- Soulseek: загрузка и раскладка ----------
# Качается только отправленное руками (вкладка Soulseek — свой план). Пир не отдаёт (ошибка, отказ,
# ушёл, часами в очереди, загрузка встала) — следующий вариант из найденных; кончились варианты —
# поиск заново и загрузка найденного (до SLSK_CYCLES раз), потом «не скачалось».

SLSK_ACTIVE = 4              # релизов качается одновременно
SLSK_QUEUE_WAIT = 3 * 3600   # с: столько ждём, пока пир начнёт отдавать
SLSK_STALL = 1800            # с: без прогресса во время загрузки
SLSK_CYCLES = 3              # столько раз искать заново, если у всех найденных пиров не скачалось
FAILED = ("Errored", "Rejected", "TimedOut", "Cancelled", "Aborted")


def slsk_root() -> Path:
    return Path(read_json(ROOT / "config.json", {}).get("slsk_local_dir") or r"H:\data\media\music\soulseek")


def deezer_root() -> Path:
    return Path(read_json(DEEMIX_DIR / "config.json", {}).get("downloadLocation") or r"H:\data\media\music\Deezer")


def slsk_local(f: dict) -> Path | None:
    """Где slskd положил файл: <папка загрузок>/<последняя папка пира>/<имя>; иначе ищем по имени и размеру."""
    root = slsk_root()
    p = root / f["dir"].rsplit("\\", 1)[-1] / f["name"]
    if p.exists():
        return p
    for d in os.scandir(root):
        if d.is_dir():
            q = Path(d.path) / f["name"]
            if q.exists() and q.stat().st_size == f["size"]:
                return q
    return None


def slsk_candidates(it: dict, manual: bool) -> list[dict]:
    r = it.get("result") or {}
    cands = [c for c in [r.get("best")] + (r.get("alternatives") or []) if c and c.get("files")]
    if not manual:  # сами — только полный релиз в нормальном качестве
        cands = [c for c in cands if c["matched"] == c["total"] and c["quality"] >= slsk.Q_320]
    return cands


def slsk_start(it: dict, idx: int) -> bool:
    cands = slsk_candidates(it, it.get("manual", False))
    tried = {tuple(t) for t in it.get("dl", {}).get("tried", [])}
    for i, c in enumerate(cands):
        if i < idx or (c["user"], c["dir"]) in tried:
            continue
        try:
            slsk.download(c["user"], c["files"])
        except requests.RequestException as exc:
            journal.log.warning("slsk download %s %s: %r", it["id"], c["user"], exc)
            tried.add((c["user"], c["dir"]))
            continue
        with slsk_lock:
            it["status"] = "downloading"
            it["dl"] = {"user": c["user"], "dir": c["dir"], "files": c["files"], "format": c["format"],
                        "how": c.get("how"), "query": c.get("query"),
                        "started": int(time.time()), "progress": 0, "last_bytes": 0, "last_change": int(time.time()),
                        "idx": i, "tried": [list(t) for t in tried | {(c["user"], c["dir"])}]}
        event("slsk_download", id=it["id"], title=f"{it['artist']} — {it['title']}", user=c["user"],
              format=c["format"], files=len(c["files"]))
        return True
    with slsk_lock:
        dl = it.setdefault("dl", {})
        dl["ended"] = int(time.time())
        if it.get("cycles", 0) < SLSK_CYCLES:  # пиры ушли/отказали — поищем, кто ещё раздаёт
            it["cycles"] = it.get("cycles", 0) + 1
            it["status"], it["want"] = "queued", True
            dl["tried"] = []
        else:
            it["status"] = "dl_failed"
    if it["status"] == "queued":
        event("slsk_research", id=it["id"], title=f"{it['artist']} — {it['title']}", tried=len(tried),
              cycle=it["cycles"], reason="у найденных пиров не скачалось")
    else:
        event("slsk_dl_failed", id=it["id"], title=f"{it['artist']} — {it['title']}", tried=len(tried))
    return False


def slsk_meta(it: dict) -> tuple[Path, list[dict], bytes | None, bool]:
    """Куда класть и с какими тегами: (папка, теги на каждый ожидаемый трек, обложка, сингл-файлом)."""
    root = deezer_root()
    if it["mode"] == "track":
        t = deezer(f"track/{it['id'][3:]}")
        alb = deezer(f"album/{t['album']['id']}")
        aa = (alb.get("artist") or {}).get("name") or t["artist"]["name"]
        various = aa in ("Various Artists", "Различные исполнители") or (alb.get("artist") or {}).get("id") == 5080
        aa_dir = "Various Artists" if various else aa
        single = alb.get("nb_tracks") == 1
        if single:
            dest = root / tagger.safe(t["artist"]["name"])
        else:
            dest = tagger.find_album_dir(root, alb["title"], [aa_dir, aa, "Various Artists", "Различные исполнители"]) \
                or tagger.album_dir(root, aa_dir, alb["title"])
            disks = {x.get("disk_number") for x in (alb.get("tracks") or {}).get("data", [])}
            if len(disks) > 1 or (t.get("disk_number") or 1) > 1:
                dest = dest / f"CD{t.get('disk_number') or 1}"
        tags = [{"title": t["title"], "artist": t["artist"]["name"],
                 "artists": [c["name"] for c in t.get("contributors") or []], "album": alb["title"],
                 "albumartist": aa_dir, "tracknumber": t.get("track_position"), "discnumber": t.get("disk_number"),
                 "date": alb.get("release_date"), "genre": [g["name"] for g in (alb.get("genres") or {}).get("data", [])],
                 "isrc": t.get("isrc"), "length": t.get("duration"), "barcode": alb.get("upc"),
                 "label": alb.get("label"), "bpm": t.get("bpm") or None,
                 "_name": f"{t['artist']['name']} - {t['title']}" if single else f"{t.get('track_position') or 0:02d} - {t['title']}",
                 "_ids": {"DEEZER_ALBUM_ID": alb.get("id"), "DEEZER_TRACK_ID": t.get("id")}}]
        return dest, tags, tagger.fetch_cover(alb.get("cover_xl")), single
    rid = it["id"][3:]
    rel = beatport.get(f"/catalog/releases/{rid}/")
    arts = [a["name"] for a in rel.get("artists", [])]
    aa = "Various Artists" if len(arts) >= beatport.VARIOUS_MIN else ", ".join(arts) or it["artist"]
    tracks = it.get("tracks") or []
    single = len(tracks) == 1
    dest = root / tagger.safe(aa) if single else tagger.album_dir(root, aa, rel.get("name") or it["title"])
    img = (rel.get("image") or {}).get("dynamic_uri", "").replace("{w}", "1200").replace("{h}", "1200") or \
        (rel.get("image") or {}).get("uri")
    tags = []
    for t in tracks:
        ta = t.get("artists") or arts
        tags.append({"title": t["title"], "artist": ", ".join(ta), "artists": ta, "album": rel.get("name") or it["title"],
                     "albumartist": aa, "tracknumber": t.get("position"), "discnumber": 1,
                     "date": rel.get("new_release_date"), "genre": [t["genre"]] if t.get("genre") else [],
                     "isrc": t.get("isrc"), "length": t.get("duration"), "barcode": rel.get("upc"),
                     "label": (rel.get("label") or {}).get("name"), "catno": rel.get("catalog_number"), "bpm": t.get("bpm"),
                     "_name": f"{', '.join(ta)} - {t['title']}" if single else f"{t.get('position') or 0:02d} - {t['title']}",
                     "_ids": {"BEATPORT_RELEASE_ID": rid, "BEATPORT_TRACK_ID": t.get("id")}})
    return dest, tags, tagger.fetch_cover(img), single


def slsk_src_format(f: dict) -> str:
    """Что было у пира до нашей перегонки: 'WAV 16/44.1', 'FLAC 24/96', 'MP3 320'."""
    ext = f["ext"].upper()
    if f.get("bitdepth") and f.get("samplerate"):
        return f"{ext} {f['bitdepth']}/{f['samplerate'] / 1000:g}"
    br = f.get("bitrate") or (round(f["size"] * 8 / 1000 / f["length"]) if f.get("length") else None)
    return f"{ext} {br}" if br else ext


def slsk_custom(dl: dict, f: dict, t: dict, date: str | None = None) -> dict:
    """Свои поля в теги: откуда файл, как сопоставлен, ID трека/релиза в каталоге."""
    return {"SOURCE": "Soulseek", "SOURCE_DATE": date or time.strftime("%Y-%m-%d"),
            "SOURCE_FORMAT": slsk_src_format(f), "SOULSEEK_USER": dl["user"], "SOULSEEK_FILE": f["path"],
            "MATCH": f"{dl.get('how') or '?'}: {dl.get('query') or ''}".strip(),
            "MATCH_DURATION": f"{t.get('length') or '?'}/{f.get('length') or '?'}"} | t.get("_ids", {})


def slsk_place(it: dict) -> None:
    dl = it["dl"]
    dest, tags, cover, single = slsk_meta(it)
    placed = []
    for f in dl["files"]:
        src = slsk_local(f)
        if not src:
            raise RuntimeError(f"скачанный файл не нашёлся в {slsk_root()}: {f['name']}")
        t = tags[f.get("track", 0)] if f.get("track", 0) < len(tags) else tags[0]
        tags_ = {k: v for k, v in t.items() if not k.startswith("_")}
        tags_["custom"] = slsk_custom(dl, f, t)
        placed.append(str(tagger.place(src, dest, t["_name"], tags_, cover, single)))
        try:  # пустую папку загрузки slskd — убрать
            src.parent.rmdir()
        except OSError:
            pass
    with slsk_lock:
        it["status"] = "placed"
        it["placed"] = placed
        dl["ended"] = int(time.time())
    event("slsk_placed", id=it["id"], title=f"{it['artist']} — {it['title']}", files=placed)


def slsk_poll(it: dict) -> None:
    dl = it["dl"]
    tr = slsk.transfers(dl["user"])
    states = [tr.get(f["path"]) for f in dl["files"]]
    now = time.time()
    total = sum(f["size"] for f in dl["files"]) or 1
    done = sum((s or {}).get("bytesTransferred", 0) for s in states)
    with slsk_lock:
        dl["progress"] = round(100 * done / total)
        if done != dl.get("last_bytes"):
            dl["last_bytes"], dl["last_change"] = done, int(now)
    if all(s and s.get("state") == slsk.DONE_OK for s in states):
        try:
            slsk_place(it)
        except Exception as exc:  # noqa: BLE001
            journal.log.exception("slsk place %s", it["id"])
            with slsk_lock:
                it["status"], it["error"] = "place_failed", repr(exc)[:300]
            event("slsk_place_failed", id=it["id"], title=f"{it['artist']} — {it['title']}", error=repr(exc)[:300])
        return
    bad = any(s and any(w in s.get("state", "") for w in FAILED) for s in states) or any(s is None for s in states)
    waiting = done == 0 and now - dl["started"] > SLSK_QUEUE_WAIT
    stalled = done > 0 and now - dl["last_change"] > SLSK_STALL
    if bad or waiting or stalled:
        why = "ошибка у пира" if bad else "пир не начал отдавать" if waiting else "загрузка встала"
        for f, s in zip(dl["files"], states):
            if s and s.get("state") != slsk.DONE_OK:
                slsk.cancel(dl["user"], s["id"])
        event("slsk_switch", id=it["id"], title=f"{it['artist']} — {it['title']}", user=dl["user"], reason=why)
        slsk_start(it, 0)  # следующий вариант (уже пробованные пропускаются)


def slsk_downloader() -> None:
    while True:
        try:
            with slsk_lock:
                active = [it for it in slsk_items.values() if it.get("status") == "downloading"]
                ready = [it for it in slsk_items.values() if it.get("status") == "download_requested"]
            for it in active:
                slsk_poll(it)
            for it in ready[:max(0, SLSK_ACTIVE - len(active))]:
                slsk_start(it, 0)
            if active or ready:
                slsk_save()
        except Exception:  # noqa: BLE001
            journal.log.exception("slsk_downloader")
        time.sleep(20)


def slsk_view() -> dict:
    with slsk_lock:
        items = json.loads(json.dumps(list(slsk_items.values())))
    for it in items:  # в ответ — без полного списка файлов альтернатив, он большой
        r = it.get("result") or {}
        r["alternatives"] = [{k: a.get(k) for k in ("user", "dir", "format", "matched", "total", "slot", "queue")}
                             for a in r.get("alternatives") or []]
    return {"items": items, "searching": list(slsk_state["searching"]), "last_sync": slsk_state["last_sync"],
            "error": slsk_state["error"]}


def slsk_send(ids: list[str]) -> dict:
    """Отправить в загрузку: найденное (полностью или частично) — качать лучший вариант; не найденное
    или ещё не искавшееся — найти и сразу качать. Неполный/низкое качество качается как есть (manual)."""
    sent = []
    with slsk_lock:
        for i in ids:
            it = slsk_items.get(i)
            if not it or it.get("status") in ("downloading", "placed", "download_requested", "dismissed"):
                continue
            r = it.get("result") or {}
            it["want"], it["cycles"] = True, 0
            it.pop("dl", None)
            it["manual"] = not r.get("checkable")
            if r.get("best") and it.get("status") in ("found", "partial", "dl_failed"):
                it["status"] = "download_requested"
            elif it.get("status") != "searching":
                it["status"] = "queued"
            sent.append(i)
    slsk_save()
    event("slsk_send", count=len(sent), titles=[f"{slsk_items[i]['artist']} — {slsk_items[i]['title']}" for i in sent])
    return {"ok": True, "sent": len(sent)}


def slsk_action(body: dict) -> dict:
    act = body.get("action")
    with slsk_lock:
        it = slsk_items.get(body.get("id"))
        if not it:
            return {"error": "нет такого"}
        if act in ("research", "restore"):
            it["status"] = "queued"
        elif act == "pick":
            it["pick"] = bool(body.get("on"))
            act = "pick_on" if it["pick"] else "pick_off"
        elif act == "dismiss":
            it["status"] = "dismissed"
        else:
            return {"error": f"неизвестное действие {act}"}
    slsk_save()
    if not act.startswith("pick"):
        event("slsk_" + act, id=it["id"], title=it.get("title"))
    return {"ok": True}


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data, code: int = 200) -> None:
        self.send(json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8", code)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(url.query)
        q = qs.get("q", [""])[0]
        types = set((qs.get("types", [""])[0] or ",".join(DEFAULT_TYPES)).split(","))
        routes = {
            "/api/data": load_data,
            "/api/deezer/artist": lambda: deezer_artist(q),
            "/api/deezer/album": lambda: deezer_album(q),
            "/api/deezer/track": lambda: deezer_track(q, qs.get("artist", [""])[0], qs.get("title", [""])[0]),
            "/api/plan": lambda: plan_response(types, qs.get("topup", [""])[0] == "1"),
            "/api/deezer/discography": lambda: discography_view(int(qs.get("id", ["0"])[0])),
            "/api/snapshot": snapshot_info,
            "/api/lib/tracks": lambda: lib_track_candidates(qs.get("artist", [""])[0], qs.get("title", [""])[0]),
            "/api/deezer/find_artist": lambda: {"artist": find_artist(q)},
            "/api/match/status": lambda: match_state | {"stored": len(matches)},
            "/api/match/one": lambda: match_for(qs.get("key", [""])[0]),
            "/api/search": lambda: search_all(q),
            "/api/deezer/related": lambda: related_artists(int(qs.get("id", ["0"])[0])),
            "/api/lib/artist": lambda: library_for(q),
            "/api/evidence": lambda: evidence(qs.get("kind", [""])[0], qs.get("key", [""])[0]),
            "/api/sent": lambda: read_json(DOWNLOADS, {}),
            "/api/queue": deemix.queue,
            "/api/discogs/confirm": lambda: discogs_confirm(int(qs.get("artist_id", ["0"])[0])),
            "/api/beatport/confirm": lambda: beatport_confirm(int(qs.get("artist_id", ["0"])[0])),
            "/api/beatport/labels": lambda: {"labels": beatport.search_labels(q)},
            "/api/beatport/label": lambda: label_view(int(qs.get("id", ["0"])[0])),
            "/api/plan/urls": lambda: planned_urls(types),
            "/api/plan/artists": plan_artists,
            "/api/downloads/audit": audit_logged,
            "/api/slsk": slsk_view,
            "/api/events": lambda: journal.read_events(int(qs.get("limit", ["500"])[0]), q,
                                                     qs.get("problems", [""])[0] == "1"),
            "/api/deezer/discography_sizes": lambda: discography_sizes(int(qs.get("id", ["0"])[0])),
        }
        try:
            if url.path == "/":
                self.send((ROOT / "review.html").read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/decisions":
                self.send((ROOT / "decisions.html").read_bytes(), "text/html; charset=utf-8")
            elif url.path in routes:
                self.send_json(routes[url.path]())
            else:
                self.send_json({"error": "not found"}, 404)
        except RuntimeError as exc:  # наши понятные сообщения — как есть
            event("error", path=url.path, error=str(exc))
            self.send_json({"error": str(exc)}, 500)
        except Exception as exc:
            journal.log.exception("GET %s", self.path)
            event("error", path=url.path, error=repr(exc))
            self.send_json({"error": repr(exc)}, 500)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        t0 = time.time()
        try:
            self._post(body)
        except Exception as exc:
            journal.log.exception("POST %s", self.path)
            event("error", path=self.path, error=repr(exc))
            self.send_json({"error": repr(exc)}, 500)
        finally:
            journal.log.info("POST %s %.1fs", self.path, time.time() - t0)

    def _post(self, body: dict) -> None:
        if self.path == "/api/decision":
            with lock:
                decisions = read_json(DECISIONS, {})
                key = f'{body["kind"]}:{body["key"]}'
                old = None
                if body.get("decision"):
                    decisions[key] = {k: v for k, v in body.items() if k != "key"} | {"ts": int(time.time())}
                else:
                    old = decisions.pop(key, None)
                write_json(DECISIONS, decisions)
            event("decision", key=key, decision=body.get("decision") or "сброшено",
                  name=body.get("name") or (old or {}).get("name"))
            self.send_json({"ok": True})
        elif self.path == "/api/send":
            self.send_json(send_to_deemix(body["selection"], body.get("bitrate"),
                                          set(body.get("types") or DEFAULT_TYPES), bool(body.get("top_up"))))
        elif self.path == "/api/match/pick":
            self.send_json(pick_match(body["key"], int(body["pick"])))
        elif self.path == "/api/plan/check":
            self.send_json(set_checks(body["key"], body["marks"]))
        elif self.path == "/api/slsk/action":
            self.send_json(slsk_action(body))
        elif self.path == "/api/slsk/send":
            self.send_json(slsk_send(body.get("ids") or []))
        elif self.path == "/api/plan/reset":
            clear_checks(body["keys"])
            event("plan_reset", keys=body["keys"])
            self.send_json({"ok": True})
        elif self.path == "/api/dismiss":
            self.send_json(dismiss(body["keys"]))
        elif self.path == "/api/refresh":
            with lock:
                self.send_json(refresh_library())
        elif self.path == "/api/downloads/retry":
            self.send_json(retry_downloads())
        elif self.path == "/api/deemix/clear":
            event("deemix_clear_finished")
            self.send_json(deemix.post("removeFinishedDownloads", {}))
        else:
            self.send_json({"error": "not found"}, 404)


if __name__ == "__main__":
    import atexit
    print(f"http://127.0.0.1:{PORT}")
    event("server_start")
    atexit.register(lambda: event("server_stop"))
    threading.Thread(target=watch_queue, daemon=True).start()
    threading.Thread(target=slsk_syncer, daemon=True).start()
    for _ in range(slsk.PARALLEL):  # по поиску на поток: slskd больше двух сразу не берёт
        threading.Thread(target=slsk_worker, daemon=True).start()
    threading.Thread(target=slsk_downloader, daemon=True).start()
    start_matching()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
