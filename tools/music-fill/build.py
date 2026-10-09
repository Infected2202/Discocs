"""Сведение raw/ в таблицы out/. raw/ только читается; out/ можно удалять и пересобирать.

out/events.csv          — все события из всех источников в одном формате
out/artists.csv         — сводка по артистам + наличие в Navidrome
out/albums_missing.csv  — альбомы, которые слушал/лайкал, но которых нет в Navidrome
out/ugc_unparsed.csv    — лайкнутые видео, где исполнителя не удалось определить
"""

import csv
import datetime
import glob
import json
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).parent
RAW = ROOT / "raw"
OUT = ROOT / "out"


def load(path: str):
    return json.loads((RAW / path).read_text(encoding="utf-8"))


# ---------- нормализация ----------

FEAT_RE = re.compile(r"\s+(?:feat\.?|ft\.?|featuring|при уч\.?)\s+.*$", re.I)


def strip_latin_marks(s: str) -> str:
    """ü→u, é→e только для латиницы (кириллическую й не трогаем)."""
    out = []
    for ch in unicodedata.normalize("NFD", s):
        if unicodedata.combining(ch) and out and out[-1].isascii():
            continue
        out.append(ch)
    return unicodedata.normalize("NFC", "".join(out))


def words(s: str | None) -> str:
    if not s:
        return ""
    s = strip_latin_marks(unicodedata.normalize("NFKC", s).casefold()).replace("ё", "е")
    s = s.translate(str.maketrans({"ø": "o", "æ": "ae", "ß": "ss", "ł": "l", "đ": "d", "œ": "oe"}))
    s = re.sub(r"['’`´]", "", s)  # 5’nizza → 5nizza
    s = s.replace("&", " and ")
    s = re.sub(r"[^\w]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm(s: str | None) -> str:
    """Ключ артиста: без пробелов, чтобы '5 nizza' == '5nizza'."""
    s = re.sub(r"\s+-\s+topic$", "", s or "", flags=re.I)
    return re.sub(r"^the ", "", words(s)).replace(" ", "")


def primary_artist(s: str | None) -> str:
    """Главный артист из строки вида 'A feat. B' / 'A, B'."""
    if not s:
        return ""
    s = FEAT_RE.sub("", s)
    return re.split(r"\s*[,;/]\s*", s)[0].strip()


TITLE_GROUP_RE = re.compile(r"[(\[]([^()\[\]]*)[)\]]")
TITLE_FEAT_RE = re.compile(r"^(?:feat\.?|ft\.?|featuring|with)\s+(.+)$", re.I)
TITLE_REMIX_RE = re.compile(r"^(\S.*?)\s+(?:remix|rmx|rework|bootleg|refix|flip|edit|re-edit|version|dub|vip|mix)$", re.I)
TITLE_NAMES_SPLIT_RE = re.compile(r"\s*[,;]\s*|\s+(?:&|x|vs\.?|feat\.?|ft\.?)\s+", re.I)


def title_artists(title: str | None) -> set[str]:
    """Артисты из названия трека: '(ft. RLGN)', '(Solomun Remix)', '[A & B Edit]' — и целиком,
    и по частям. Для «участия» хватает: там ещё и название альбома должно совпасть точно."""
    names = set()
    for group in TITLE_GROUP_RE.findall(title or ""):
        group = group.strip()
        m = TITLE_FEAT_RE.match(group) or TITLE_REMIX_RE.match(group)
        if m:
            whole = m.group(1).strip()
            names.add(whole)
            names.update(p.strip() for p in TITLE_NAMES_SPLIT_RE.split(whole) if p.strip())
    return names


MOJIBAKE_RE = re.compile(r"[À-ÿ¨¸]{3,}")


def fix_mojibake(s: str | None) -> str:
    """'Âëàæíûå Âàòðóøêè' (cp1251, прочитанный как latin-1) → 'Влажные Ватрушки'."""
    if not s or not MOJIBAKE_RE.search(s):
        return s or ""
    try:
        fixed = s.encode("latin-1").decode("cp1251")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s
    return fixed if re.search(r"[а-яА-ЯёЁ]{3,}", fixed) else s


def norm_album(s: str | None) -> str:
    s = re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]?", " ", s or "")  # все скобки, в т.ч. обрезанные
    s = re.sub(r"^\s*(?:19|20)\d\d\s*[-–.]\s*", "", s)         # '2006 - Billy Talent II'
    s = words(s)
    s = re.sub(r"\bvol(?:ume)?\b", "vol", s)
    s = re.sub(r"\b(?:cd|disc|disk|lp)\s*\d*$", "", s).strip()
    return s


ROMAN_OR_NUM_RE = re.compile(r"^(?:[ivx]+|\d+)\b")


def album_matches(heard: str, lib: str) -> bool:
    if heard == lib:
        return True
    short, long_ = sorted((heard, lib), key=len)
    if len(short.split()) >= 2 and long_.startswith(short + " "):
        # 'billy talent' не должно покрывать 'billy talent ii'
        return not ROMAN_OR_NUM_RE.match(long_[len(short) + 1:])
    if len(short) >= 12 and long_.startswith(short):  # обрезанные названия last.fm
        return True
    return SequenceMatcher(None, heard, lib).ratio() >= 0.88


UGC_SPLIT_RE = re.compile(r"\s+[-–—]\s+")
UGC_JUNK_RE = re.compile(r"\b(?:mix|set|live|dj set|podcast|session|sessions|compilation|full album)\b", re.I)


CATALOG_RE = re.compile(r"^(?:[A-Za-z]{1,6}[ _-]?\d{2,5}[A-Za-z]?|premiere:?|free download:?)$", re.I)
PREFIX_RE = re.compile(r"^(?:premiere|free download|exclusive)\s*:\s*", re.I)


def parse_ugc_title(title: str) -> tuple[str, str] | None:
    """'Artist - Track [label]' → (artist, track). None если не похоже на трек.
    Каталожный номер лейбла впереди отрезается: 'AL025 - Denis Horvat - Noise' → Denis Horvat."""
    title = PREFIX_RE.sub("", title.strip())
    parts = UGC_SPLIT_RE.split(title)
    while len(parts) > 2 and CATALOG_RE.match(parts[0].strip(" []")):
        parts = parts[1:]
    parts = [parts[0], " - ".join(parts[1:])] if len(parts) >= 2 else parts
    if len(parts) != 2:
        return None
    artist, track = parts[0].strip(" \"'"), parts[1]
    if not artist or len(artist) > 60:
        return None
    return primary_artist(artist), re.sub(r"\s*[\[\(|#].*$", "", track).strip()


# ---------- события ----------

EVENT_FIELDS = ["source", "ts", "artist", "album", "title", "extra"]


def ytm_artist(t: dict) -> str:
    arts = t.get("artists") or []
    return (arts[0].get("name") or "") if arts else ""


def collect_events() -> tuple[list[dict], list[dict]]:
    events: list[dict] = []
    ugc_unparsed: list[dict] = []

    def add(source, artist, album="", title="", ts=None, extra=""):
        events.append({"source": source, "ts": ts, "artist": artist or "",
                       "album": album or "", "title": title or "", "extra": extra})

    # Last.fm
    for f in sorted(glob.glob(str(RAW / "lastfm/scrobbles/page_*.json"))):
        for t in json.loads(Path(f).read_text(encoding="utf-8"))["recenttracks"].get("track", []):
            if t.get("@attr", {}).get("nowplaying"):
                continue
            ts = int(t["date"]["uts"])
            if ts < 946684800:  # битые метки «1970»
                continue
            add("lf_scrobble", t["artist"]["name"], t["album"]["#text"], t["name"], ts)
    for f in sorted(glob.glob(str(RAW / "lastfm/loved/page_*.json"))):
        for t in json.loads(Path(f).read_text(encoding="utf-8"))["lovedtracks"].get("track", []):
            add("lf_loved", t["artist"]["name"], "", t["name"], int(t["date"]["uts"]))

    # YouTube Music
    for t in load("ytm/liked_songs.json")["tracks"]:
        album = (t.get("album") or {}).get("name", "")
        if t.get("videoType") == "MUSIC_VIDEO_TYPE_UGC" and not album:
            parsed = parse_ugc_title(t["title"])
            channel = ytm_artist(t)
            if parsed and not UGC_JUNK_RE.search(t["title"]):
                add("ytm_like_ugc", parsed[0], "", parsed[1], extra=f"channel={channel}")
            else:
                ugc_unparsed.append({"channel": channel, "title": t["title"],
                                     "videoId": t.get("videoId", "")})
        else:
            add("ytm_like", primary_artist(ytm_artist(t)), album, t["title"],
                extra=t.get("videoType") or "")
    for a in load("ytm/library_artists.json"):
        add("ytm_lib_artist", a["artist"])
    for a in load("ytm/library_subscriptions.json"):
        add("ytm_subscription", a["artist"])
    for t in load("ytm/library_songs.json"):
        add("ytm_lib_song", primary_artist(ytm_artist(t)), (t.get("album") or {}).get("name", ""), t["title"])
    for a in load("ytm/library_albums.json"):
        add("ytm_lib_album", primary_artist(ytm_artist(a)), a.get("title", ""))
    for t in load("ytm/history.json"):
        add("ytm_history", primary_artist(ytm_artist(t)), (t.get("album") or {}).get("name", ""), t["title"])

    return events, ugc_unparsed


# ---------- библиотека Navidrome ----------

def library_index():
    lib_artist_albums: dict[str, set[str]] = defaultdict(set)
    lib_artist_songs: Counter = Counter()
    lib_album_titles: dict[str, set[str]] = defaultdict(set)  # название → артисты (как в Navidrome)
    # артист → точные названия (words) альбомов, где он альбомный артист или артист хоть одного
    # трека: сборник записан на Various Artists, а участник — только на своём треке. Для строгого
    # сопоставления «участия»; отдельно от lib_artist_albums, чтобы не раздувать число альбомов артиста
    lib_artist_appears: dict[str, set[str]] = defaultdict(set)

    def artist_names(item: dict, *keys) -> set[str]:
        names = set()
        for k in keys:
            v = item.get(k)
            if isinstance(v, str):
                names.add(v)
                names.add(primary_artist(v))
            elif isinstance(v, list):
                names.update(x.get("name", "") for x in v)
        return {norm(fix_mojibake(n)) for n in names if n}

    mojibake = {}
    for s in load("navidrome/songs.json"):
        for k in ("artist", "album", "title"):
            if fix_mojibake(s.get(k)) != (s.get(k) or ""):
                mojibake[s["path"]] = {"path": s["path"], "field": k,
                                       "as_is": s[k], "fixed": fix_mojibake(s[k])}
    with open(OUT / "navidrome_mojibake.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, ["path", "field", "as_is", "fixed"])
        w.writeheader()
        w.writerows(mojibake.values())

    for a in load("navidrome/albums.json"):
        na = norm_album(fix_mojibake(a.get("name")))
        lib_album_titles[na].add(fix_mojibake(a.get("artist", "")))
        for n in artist_names(a, "artist", "artists", "albumArtists"):
            lib_artist_albums[n].add(na)
    for s in load("navidrome/songs.json"):
        exact = words(fix_mojibake(s.get("album")))
        for n in artist_names(s, "artist", "artists", "albumArtists", "displayArtist"):
            lib_artist_songs[n] += 1
            lib_artist_appears[n].add(exact)
        # фит/ремикс в названии ('Pi Pu Pa (ft. RLGN)') и тег REMIXER — тоже участие в альбоме
        credited = title_artists(fix_mojibake(s.get("title")))
        credited.update(c["artist"]["name"] for c in s.get("contributors") or []
                        if c.get("role") == "remixer" and (c.get("artist") or {}).get("name"))
        for name in credited:
            lib_artist_appears[norm(fix_mojibake(name))].add(exact)
    for a in load("navidrome/artists.json"):
        lib_artist_albums.setdefault(norm(fix_mojibake(a["name"])), set())
    return lib_artist_albums, lib_artist_songs, lib_album_titles, lib_artist_appears


# ---------- сводка ----------

def year(ts):
    return datetime.datetime.fromtimestamp(ts).year if ts else None


def build():
    OUT.mkdir(exist_ok=True)  # до library_index: он пишет navidrome_mojibake.csv
    events, ugc_unparsed = collect_events()
    lib_artist_albums, lib_artist_songs, lib_album_titles, _ = library_index()

    with open(OUT / "events.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, EVENT_FIELDS)
        w.writeheader()
        w.writerows(events)

    with open(OUT / "ugc_unparsed.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, ["channel", "title", "videoId"])
        w.writeheader()
        w.writerows(ugc_unparsed)

    # --- артисты ---
    display: dict[str, Counter] = defaultdict(Counter)
    stats: dict[str, Counter] = defaultdict(Counter)
    years: dict[str, list[int]] = defaultdict(list)
    tracks: dict[str, set[str]] = defaultdict(set)
    albums_heard: dict[tuple[str, str], Counter] = defaultdict(Counter)
    album_tracks_heard: dict[tuple[str, str], set] = defaultdict(set)  # какие треки альбома слушал
    lib_album_tracks: dict[str, set] = defaultdict(set)  # альбом в Navidrome → его треки
    for s in load("navidrome/songs.json"):
        lib_album_tracks[norm_album(fix_mojibake(s.get("album")))].add(title_key(fix_mojibake(s.get("title"))))
    album_display: dict[tuple[str, str], Counter] = defaultdict(Counter)

    for e in events:
        key = norm(e["artist"])
        if not key:
            continue
        display[key][e["artist"]] += 1
        src = e["source"]
        stats[key][src] += 1
        if src == "lf_scrobble":
            y = year(e["ts"])
            years[key].append(y)
            period = "lf_2008_14" if y <= 2014 else "lf_2015_24" if y <= 2024 else "lf_2025_"
            stats[key][period] += 1
            tracks[key].add(norm(e["title"]))
        if e["album"]:
            ak = (key, norm_album(e["album"]))
            if ak[1]:
                albums_heard[ak][src] += 1
                album_tracks_heard[ak].add(title_key(e["title"]))
                album_display[ak][e["album"]] += 1

    rows = []
    for key, st in stats.items():
        lib_alb = len(lib_artist_albums.get(key, ()))
        lib_songs = lib_artist_songs.get(key, 0)
        ys = years.get(key) or []
        likes = st["ytm_like"] + st["ytm_like_ugc"]
        interest = (st["lf_scrobble"] + 10 * st["lf_loved"] + 15 * likes
                    + 30 * st["ytm_lib_artist"] + 30 * st["ytm_subscription"]
                    + 5 * st["ytm_lib_song"] + 20 * st["ytm_lib_album"] + 3 * st["ytm_history"])
        rows.append({
            "key": key,
            "artist": display[key].most_common(1)[0][0],
            "status": "missing" if lib_songs == 0 and lib_alb == 0 else "in_library",
            "interest": interest,
            "lib_albums": lib_alb,
            "lib_songs": lib_songs,
            "lf_total": st["lf_scrobble"],
            "lf_2008_14": st["lf_2008_14"],
            "lf_2015_24": st["lf_2015_24"],
            "lf_2025_": st["lf_2025_"],
            "lf_first": min(ys) if ys else "",
            "lf_last": max(ys) if ys else "",
            "lf_tracks": len(tracks.get(key, ())),
            "lf_loved": st["lf_loved"],
            "ytm_likes": likes,
            "ytm_lib_artist": st["ytm_lib_artist"],
            "ytm_sub": st["ytm_subscription"],
            "ytm_lib_songs": st["ytm_lib_song"],
            "ytm_lib_albums": st["ytm_lib_album"],
            "ytm_history": st["ytm_history"],
        })
    rows.sort(key=lambda r: -r["interest"])
    with open(OUT / "artists.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # --- альбомы, которых нет ---
    artist_display = {r["key"]: r["artist"] for r in rows}
    alb_rows = []
    for (akey, alb), st in albums_heard.items():
        plays, likes = st["lf_scrobble"], st["ytm_like"]
        lib_songs = st["ytm_lib_song"] + st["ytm_lib_album"]
        if plays < 5 and not likes and not lib_songs:
            continue
        if any(album_matches(alb, la) for la in lib_artist_albums.get(akey, ())):
            continue
        # тот же альбом у артиста с другим написанием имени ('30 Seconds to Mars' / 'Thirty Seconds
        # to Mars', транслит, соавторы) — это не дыра
        if any(artists_similar(artist_display.get(akey, akey), a) for a in lib_album_titles.get(alb, ())):
            continue
        # альбом с тем же названием есть и в нём те самые треки — это он, даже если записан на
        # другого артиста (сборники 'Various Artists', японское/латинское имя)
        heard = album_tracks_heard[(akey, alb)] - {""}
        if heard & lib_album_tracks.get(alb, set()):
            continue
        alb_rows.append({
            "key": f"{akey}|{alb}",
            "artist": artist_display.get(akey, akey),
            "album": album_display[(akey, alb)].most_common(1)[0][0],
            "artist_in_library": "yes" if akey in lib_artist_albums or lib_artist_songs.get(akey) else "no",
            # то же название у другого артиста: '30 Seconds to Mars' vs 'Thirty Seconds to Mars'
            "maybe_in_lib_as": " / ".join(sorted(lib_album_titles.get(alb, ()))[:3]),
            "lf_plays": plays,
            "ytm_likes": likes,
            "ytm_lib": lib_songs,
        })
    alb_rows.sort(key=lambda r: -(r["lf_plays"] + 15 * r["ytm_likes"] + 10 * r["ytm_lib"]))
    with open(OUT / "albums_missing.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, list(alb_rows[0].keys()))
        w.writeheader()
        w.writerows(alb_rows)

    # --- сводка в консоль ---
    missing = [r for r in rows if r["status"] == "missing"]
    print(f"events: {len(events)}  ugc unparsed: {len(ugc_unparsed)}")
    print(f"artists: {len(rows)}  in library: {len(rows) - len(missing)}  missing: {len(missing)}")
    print(f"albums missing (filtered): {len(alb_rows)}")

    build_playlists(lib_artist_albums, lib_artist_songs)


# ---------- плейлисты ----------

TITLE_NOISE_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]|\s+(?:feat\.?|ft\.?)\s.*$", re.I)


def norm_title(s: str | None) -> str:
    return words(TITLE_NOISE_RE.sub("", s or ""))


def title_key(s: str | None) -> str:
    """Название для сравнения: без скобок/feat и в транслите ('Пару строк' == 'Paru strok')."""
    return norm_title(s).translate(CYR_LAT)


def lib_songs_by_artist() -> dict[str, list[tuple[str, str, str, str]]]:
    """артист → [(ключ названия, название, альбом, id в Navidrome)]."""
    by_artist: dict[str, list] = defaultdict(list)
    for s in load("navidrome/songs.json"):
        title, album = fix_mojibake(s.get("title")), fix_mojibake(s.get("album"))
        entry = (title_key(title), title, album, s["id"])
        names = {s.get("artist"), s.get("displayArtist")} | {a.get("name") for a in s.get("artists") or []}
        keys = {norm(fix_mojibake(n)) for n in names if n} | {norm(primary_artist(fix_mojibake(n))) for n in names if n}
        for k in keys:
            by_artist[k].append(entry)
    return by_artist


CYR_LAT = str.maketrans({"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z",
                         "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
                         "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch",
                         "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"})


def artist_parts(name: str) -> set[str]:
    """'Sakdat & Balaur' → {'sakdat', 'balaur'} в транслите; для нестрогого сравнения артистов."""
    # делим исходное имя: words() превращает ',', ';', '/', '•' в пробелы — после него делить поздно
    raw = re.split(r"\s*(?:&|,|;|•|·)\s*|\s+/\s+", name or "")  # 'AC/DC' — одно имя, ' / ' — разделитель
    parts = [p for r in raw for p in re.split(r"\s+(?:x|and|feat\.?|ft\.?|vs\.?)\s+", words(r))]
    return {p.replace(" ", "").translate(CYR_LAT) for p in parts if len(p) >= 2}


def artists_similar(a: str, b: str) -> bool:
    """Тот же артист в другом написании: транслит (Haski/Хаски), опечатка (Apache/Apachi),
    соавторы через другой разделитель (Sakdat & Balaur / Sakdat • Balaur • Moldovan)."""
    pa, pb = artist_parts(a), artist_parts(b)
    if not pa or not pb:
        return False
    if pa & pb:
        return True
    ka, kb = "".join(sorted(pa)), "".join(sorted(pb))
    short, long_ = sorted((ka, kb), key=len)
    return SequenceMatcher(None, ka, kb).ratio() >= 0.75 or (len(short) >= 5 and short in long_)


def title_match(yt: str, lib: str) -> str | None:
    """'exact' | 'fuzzy' | None. 'sneaky' ~ 'sneaky acid' — fuzzy (показываем, что нашлось)."""
    if not yt or not lib:
        return None
    if yt == lib:
        return "exact"
    short, long_ = sorted((yt, lib), key=len)
    if len(short) >= 4 and long_.startswith(short + " "):
        return "fuzzy"
    if SequenceMatcher(None, yt, lib).ratio() >= 0.85:
        return "fuzzy"
    return None


def playlist_track(t: dict) -> dict:
    """Трек плейлиста → артист/название/альбом. Для UGC-видео артист берётся из названия."""
    title = t.get("title") or ""
    artist = ytm_artist(t)
    album = (t.get("album") or {}).get("name", "") if isinstance(t.get("album"), dict) else ""
    vtype = t.get("videoType") or ""
    artist = re.sub(r"\s+-\s+Topic$", "", artist)
    if (vtype == "MUSIC_VIDEO_TYPE_UGC" or t.get("_source") == "get_song") and not album:
        parsed = parse_ugc_title(title)
        if parsed:
            artist, title = parsed
    else:
        # клипы (OMV) часто называются 'Mr Oizo - M Seq (Official Video …)' — артист внутри названия
        parts = UGC_SPLIT_RE.split(title, maxsplit=1)
        if len(parts) == 2 and SequenceMatcher(None, norm(parts[0]), norm(artist)).ratio() >= 0.8:
            title = re.sub(r"\s*[\[\(|#].*$", "", parts[1]).strip() or parts[1]
    return {"videoId": t.get("videoId"), "artist": primary_artist(artist), "title": title,
            "album": album, "videoType": vtype, "channel": ytm_artist(t),
            "unavailable": bool(t.get("_error")) or t.get("_playable") not in (None, "OK")}


def build_playlists(lib_artist_albums, lib_artist_songs) -> None:
    songs = lib_songs_by_artist()
    songs_by_title: dict[str, list] = defaultdict(list)  # ключ названия → [(артист, название, альбом, id)]
    for s in load("navidrome/songs.json"):
        t = fix_mojibake(s.get("title"))
        songs_by_title[title_key(t)].append((fix_mojibake(s.get("displayArtist") or s.get("artist") or ""),
                                              t, fix_mojibake(s.get("album")), s["id"]))
    result = []
    # «Понравившиеся» в Takeout плейлистом не выгружаются — добавляем их отдельно:
    # лайки YTM (ytmusicapi) и ♥ Last.fm
    liked = load("ytm/liked_songs.json")["tracks"]
    loved = []
    for f in sorted(glob.glob(str(RAW / "lastfm/loved/page_*.json"))):
        for t in json.loads(Path(f).read_text(encoding="utf-8"))["lovedtracks"].get("track", []):
            loved.append({"videoId": f"lf:{norm(t['artist']['name'])}:{norm_title(t['name'])}",
                          "title": t["name"], "artists": [{"name": t["artist"]["name"]}],
                          "videoType": "MUSIC_VIDEO_TYPE_ATV"})
    virtual = [
        {"id": "LM", "title": "♥ Лайки YouTube Music", "tracks": liked,
         "meta": {"Playlist Visibility": "Private"}},
        {"id": "LF_LOVED", "title": "♥ Last.fm loved", "tracks": loved, "meta": {}},
    ]
    sources = virtual + [json.loads(Path(f).read_text(encoding="utf-8"))
                         for f in sorted(glob.glob(str(RAW / "ytm/playlists/*.json")))]
    for pl in sources:
        tracks = []
        for t in pl["tracks"]:
            tr = playlist_track(t)
            akey = norm(tr["artist"])
            tr["lib"] = ""  # что нашлось в библиотеке — показываем в интерфейсе
            if not akey and not tr["title"]:
                tr["status"] = "gone"  # видео удалено/недоступно, метаданных нет
                tracks.append(tr | {"key": tr["videoId"]})
                continue
            yt_title = title_key(tr["title"])
            best = None
            full = words(tr["title"]).translate(CYR_LAT)
            for nt, title, album, sid in songs.get(akey, ()):
                m = title_match(yt_title, nt)
                if m == "exact" or (m and not best):
                    if best and best[0] == "exact" and m == "exact":
                        continue  # уже есть точное; не меняем на другую версию
                    best = (m, title, album, sid)
                    # без скобок 'Пару строк' == 'Пару строк (Instrumental)' — ищем дальше
                    # версию, у которой совпадает и полное название
                    if m == "exact" and words(title).translate(CYR_LAT) == full:
                        break
            if best and best[0] == "exact" and words(best[1]).translate(CYR_LAT) != full:
                for nt, title, album, sid in songs.get(akey, ()):
                    if words(title).translate(CYR_LAT) == full:
                        best = ("exact", title, album, sid)
                        break
            if not best and yt_title:
                # второй проход: то же название у артиста с другим написанием имени
                for lib_artist, title, album, sid in songs_by_title.get(yt_title, ()):
                    if artists_similar(tr["artist"], lib_artist):
                        best = ("artist_fuzzy", title, album, sid, lib_artist)
                        break
            tr["lib_id"] = best[3] if best else None  # id трека в Navidrome — для экспорта плейлистов
            if best and best[0] == "artist_fuzzy":
                tr["status"] = "maybe_track"
                tr["lib"] = f"{best[1]} — {best[2]} (артист в библиотеке: {best[4]})"
            elif best:
                tr["status"] = "have_track" if best[0] == "exact" else "maybe_track"
                tr["lib"] = f"{best[1]} — {best[2]}"
            elif akey in songs or lib_artist_albums.get(akey):
                tr["status"] = "have_artist"
                albums = sorted({e[2] for e in songs.get(akey, ()) if e[2]})
                tr["lib"] = f"{len(songs.get(akey, ()))} тр. артиста; альбомы: " + ", ".join(albums[:6]) + (" …" if len(albums) > 6 else "")
            else:
                tr["status"] = "missing"
            tr["key"] = tr["videoId"]
            tracks.append(tr)
        meta = pl["meta"]
        result.append({"id": pl["id"], "title": pl["title"],
                       "created": meta.get("Playlist Create Timestamp", "")[:10],
                       "updated": meta.get("Playlist Update Timestamp", "")[:10],
                       "visibility": meta.get("Playlist Visibility"), "tracks": tracks})
    (OUT / "playlists.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    c = Counter(t["status"] for p in result for t in p["tracks"])
    print(f"playlists: {len(result)}  tracks: {sum(c.values())}  {dict(c)}")


if __name__ == "__main__":
    build()
