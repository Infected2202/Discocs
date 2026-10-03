"""Soulseek через slskd: то, чего нет на Deezer (снятые треки, релизы лейблов вне Deezer).

Поиск каскадом — от точного к мягкому, на каждом шаге сначала FLAC:
  1. каталожный номер ('SUARA001' ~ папка '...-(SUARA01)-WEB-2008');
  2. артист + название релиза;
  3. варианты названия (без скобок и хвоста EP, транслит);
  4. по трекам — артист + название трека.
Совпадение проверяется длительностями: slskd отдаёт длину файлов, и она сходится с Deezer/Beatport
до секунды. Без совпадения длительностей (или названий, где длины нет) кандидат не принимается.

slskd: config.json — slskd_url, slskd_api_key. Ответы поиска кэшируются (cache/slsk.json)."""
from __future__ import annotations

import collections
import json
import re
import threading
import time
import uuid
from pathlib import Path

import requests

import build

ROOT = Path(__file__).parent
CACHE = ROOT / "cache" / "slsk.json"
TTL_HIT = 3 * 86400      # у пиров всё меняется: найденное держим 3 дня
TTL_MISS = 6 * 3600     # не найденное — 6 часов (пустой ответ бывает и случайным)
SEARCH_TIMEOUT = 15000   # мс, сколько slskd собирает ответы
DUR_TOL = 3              # с, допуск длительности
# сервер Soulseek не любит частые поиски: не больше 34 за 220 с (как в slsk-batchdl), по 2 одновременно
RATE_N, RATE_WINDOW = 34, 220
PARALLEL = 2

AUDIO = {"flac", "mp3", "wav", "aif", "aiff", "m4a", "ogg", "opus", "alac", "ape", "wv"}
LOSSLESS = {"flac", "wav", "aif", "aiff", "alac", "ape", "wv"}
# ранг качества: FLAC → прочий lossless → MP3 320 → остальное (последнее только показываем)
Q_FLAC, Q_LOSSLESS, Q_320, Q_LOW = 4, 3, 2, 1
Q_NAME = {Q_FLAC: "FLAC", Q_LOSSLESS: "lossless", Q_320: "MP3 320", Q_LOW: "lossy"}

_io = threading.Lock()
_rate_lock = threading.Lock()
_sem = threading.Semaphore(PARALLEL)
_stamps: collections.deque = collections.deque()
_session = requests.Session()


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write(path: Path, data) -> None:
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


_cache: dict = _read(CACHE, {})


def _cfg() -> tuple[str, dict]:
    cfg = _read(ROOT / "config.json", {})
    if not cfg.get("slskd_api_key"):
        raise RuntimeError('нет ключа slskd — добавь "slskd_api_key" в config.json')
    return cfg.get("slskd_url", "http://192.168.1.41:5030").rstrip("/") + "/api/v0", \
        {"X-API-Key": cfg["slskd_api_key"]}


def api(method: str, path: str, **kw):
    url, headers = _cfg()
    r = _session.request(method, url + path, headers=headers, timeout=30, **kw)
    r.raise_for_status()
    return r.json() if r.content else None


# ---------- поиск ----------

def query_text(s: str) -> str:
    """Soulseek ищет по словам пути: пунктуацию — в пробелы, односимвольные слова оставляем (Vol. V)
    ('-' в начале слова — исключение из поиска, его тоже убираем)."""
    w = re.sub(r"[^\w]+", " ", (s or "").replace("_", " ")).split()
    return " ".join(w)


def _throttle() -> None:
    with _rate_lock:
        while True:
            now = time.time()
            while _stamps and now - _stamps[0] > RATE_WINDOW:
                _stamps.popleft()
            if len(_stamps) < RATE_N:
                _stamps.append(now)
                return
            time.sleep(RATE_WINDOW - (now - _stamps[0]) + 0.5)


def _file(peer: dict, f: dict) -> dict | None:
    path = f.get("filename") or ""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext not in AUDIO:
        return None
    d, _, name = path.rpartition("\\")
    return {"user": peer["username"], "path": path, "dir": d, "name": name, "ext": ext,
            "size": f.get("size") or 0, "length": f.get("length"), "bitrate": f.get("bitRate"),
            "samplerate": f.get("sampleRate"), "bitdepth": f.get("bitDepth"),
            "slot": bool(peer.get("hasFreeUploadSlot")), "queue": peer.get("queueLength") or 0,
            "speed": peer.get("uploadSpeed") or 0}


def search(text: str) -> list[dict]:
    """Аудиофайлы по запросу (все пиры вместе). Кэш по тексту запроса. Пустой ответ повторяем
    один раз: под нагрузкой сеть иногда молча не отвечает на запрос, который потом находит."""
    text = query_text(text)
    if not text:
        return []
    with _io:
        hit = _cache.get(text)
    if hit and time.time() - hit["ts"] < (TTL_HIT if hit["files"] else TTL_MISS):
        return hit["files"]
    files = _search_once(text) or _search_once(text, pause=10)
    with _io:
        _cache[text] = {"ts": time.time(), "files": files}
        _write(CACHE, _cache)
    return files


def _search_once(text: str, pause: float = 0) -> list[dict]:
    time.sleep(pause)
    with _sem:
        _throttle()
        sid = str(uuid.uuid4())
        for attempt in range(20):  # 429 — у slskd свой предел одновременных поисков: ждём очереди
            try:
                api("POST", "/searches", json={"id": sid, "searchText": text, "searchTimeout": SEARCH_TIMEOUT,
                                               "filterResponses": True, "responseLimit": 200, "fileLimit": 20000})
                break
            except requests.HTTPError as exc:
                if exc.response is None or exc.response.status_code != 429 or attempt == 19:
                    raise
                time.sleep(3)
        t0 = time.time()
        while time.time() - t0 < SEARCH_TIMEOUT / 1000 + 30:
            time.sleep(2)
            if api("GET", f"/searches/{sid}").get("isComplete"):
                break
        responses = api("GET", f"/searches/{sid}/responses") or []
        try:
            api("DELETE", f"/searches/{sid}")  # не копим поиски в интерфейсе slskd
        except requests.RequestException:
            pass
    return [x for p in responses for f in p.get("files", []) if (x := _file(p, f))]


# ---------- сравнение ----------

def fwords(s: str) -> set[str]:
    """Слова имени файла/папки: '01-owl-m97_(original_mix)-mkd.mp3' → {01, owl, m97, original, mix, mkd}."""
    s = re.sub(r"\.\w{2,4}$", "", s or "")
    return set(build.words(s.replace("_", " ")).split())


MIX_NOISE = {"original", "mix", "extended", "edit", "radio", "version", "feat", "ft", "featuring", "and", "the"}
REMIX_WORDS = {"remix", "rmx", "dub", "vip", "bootleg", "rework", "edit", "remake", "reprise", "instrumental"}


def title_words(title: str) -> set[str]:
    return set(build.words((title or "").replace("_", " ")).split()) - MIX_NOISE


def quality(f: dict) -> int:
    if f["ext"] == "flac":
        return Q_FLAC
    if f["ext"] in LOSSLESS or (f["ext"] == "m4a" and (f.get("bitrate") or 0) > 500):
        return Q_LOSSLESS
    br = f.get("bitrate")
    if not br and f.get("length"):
        br = f["size"] * 8 / 1000 / f["length"]
    return Q_320 if f["ext"] == "mp3" and (br or 0) >= 315 else Q_LOW


def track_matches(f: dict, title: str, duration: int | None, artists: list[str] | None = None) -> bool:
    """Файл = трек: все значимые слова названия есть в имени файла, ремикс не подменён оригиналом
    (и наоборот), артист — где-то в пути, длительность в допуске."""
    fw = fwords(f["name"])
    tw = title_words(title)
    if not tw or not tw <= fw:
        return False
    # 'X (Y Remix)' ≠ 'X': лишние ремиксовые слова в файле — другая версия
    if (fw & REMIX_WORDS) - tw and not (tw & REMIX_WORDS):
        return False
    if artists:
        pw = fwords(f["dir"]) | fw
        if not any(set(build.words(a).split()) - {"the", "and"} <= pw for a in artists if build.words(a)):
            return False
    if duration and f.get("length"):
        return abs(f["length"] - duration) <= DUR_TOL
    return True


def cat_tokens(s: str) -> set[str]:
    """Токены каталожного номера в пути: 'SUARA001', '(SUARA01)', 'KOSMOS050DGTL', 'ABS DIG 011'.
    Ведущие нули в числах не важны; соседние токены склеиваем ('ABS DIG 011' → 'absdig11')."""
    toks = [t for t in re.split(r"[^0-9a-z]+", build.words(s).replace(" ", " ")) if t]
    toks = [re.sub(r"(?<![0-9])0+(?=\d)", "", t) for t in toks]
    out = set(toks)
    for i in range(len(toks) - 1):
        out.add(toks[i] + toks[i + 1])
        if i + 2 < len(toks):
            out.add(toks[i] + toks[i + 1] + toks[i + 2])
    return out


def cat_key(catno: str) -> str:
    k = re.sub(r"[^0-9a-z]+", "", build.words(catno or "").replace(" ", ""))
    return re.sub(r"(?<![0-9])0+(?=\d)", "", k)


def cat_queries(catno: str) -> list[str]:
    """'SUARA001' → ['SUARA001', 'SUARA01', 'SUARA1']: в путях номер пишут с разным числом нулей."""
    catno = (catno or "").strip()
    m = re.match(r"^([A-Za-z]+)\s*[-_ ]?\s*0*(\d+)([A-Za-z]*)$", (catno or "").strip())
    if not m:
        return [catno] if catno else []
    p, n, s = m.groups()
    return list(dict.fromkeys([catno.strip()] + [f"{p}{n.zfill(w)}{s}" for w in (3, 2, 1)]))


# ---------- кандидаты ----------

def peer_rank(f: dict) -> tuple:
    return (f["slot"], -min(f["queue"], 10000), f["speed"])


def folder_candidates(files: list[dict], tracks: list[dict], artists: list[str] | None,
                      catno: str | None, how: str, query: str) -> list[dict]:
    """Папки (пир + путь), в которых нашёлся релиз: каждому треку — файл с совпавшим названием и
    длительностью. Внутри папки один формат — лучший из имеющихся."""
    by_dir = collections.defaultdict(list)
    for f in files:
        by_dir[(f["user"], f["dir"])].append(f)
    ck = cat_key(catno) if catno else ""
    out = []
    for (user, d), fs in by_dir.items():
        cat_hit = bool(ck) and len(ck) >= 4 and ck in cat_tokens(d)
        for q in sorted({quality(f) for f in fs}, reverse=True):
            pool = [f for f in fs if quality(f) == q]
            picked, used = [], set()
            for i, t in enumerate(tracks):
                cand = [f for f in pool if f["path"] not in used
                        and track_matches(f, t["title"], t.get("duration"), None if cat_hit else artists)]
                if not cand and cat_hit and t.get("duration"):
                    # папка по каталожному номеру: название в имени файла бывает обрезано — хватит длины
                    cand = [f for f in pool if f["path"] not in used and f.get("length")
                            and abs(f["length"] - t["duration"]) <= DUR_TOL]
                if cand:
                    best = min(cand, key=lambda f: abs((f.get("length") or 0) - (t.get("duration") or 0)))
                    picked.append(best | {"track": i})  # номер ожидаемого трека — для тегов при раскладке
                    used.add(best["path"])
            if not picked:
                continue
            p = picked[0]
            out.append({"user": user, "dir": d, "quality": q, "format": Q_NAME[q], "matched": len(picked),
                        "total": len(tracks), "files": picked, "how": how, "query": query, "catno": cat_hit,
                        "slot": p["slot"], "queue": p["queue"], "speed": p["speed"],
                        "size": sum(f["size"] for f in picked)})
            break  # из папки — только лучший формат
    return out


def cand_rank(c: dict) -> tuple:
    complete = c["matched"] == c["total"]
    return (complete, c["quality"], c["matched"], c["catno"], c["slot"], -min(c["queue"], 10000), c["speed"])


def strip_title(t: str) -> str:
    t = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", t or "")
    t = re.sub(r"\b(?:EP|LP|E\.P\.?|Mini LP|Single|Remixes)\s*$", " ", t.strip(), flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


def release_queries(artist: str, title: str, catno: str | None) -> list[tuple[str, str]]:
    """(как, запрос) — от точного к мягкому."""
    various = build.words(artist) in {"various artists", "various", "va", ""}
    first = re.split(r"\s*(?:,|&| x | feat\.? | ft\.? | vs\.? )\s*", artist or "", flags=re.I)[0]
    qs = [("каталожный номер", q) for q in cat_queries(catno or "") if len(cat_key(q)) >= 4]
    bare = strip_title(title)
    if not various:
        qs += [("артист + релиз", f"{first} {title}")]
        if bare and bare != title:
            qs += [("артист + релиз без скобок", f"{first} {bare}")]
        tr = bare.casefold().translate(build.CYR_LAT)
        if tr != bare.casefold():
            qs += [("артист + релиз (транслит)", f"{first} {tr}")]
    if len(build.words(bare)) >= 10 or various:
        qs += [("релиз", bare or title)]
    seen, out = set(), []
    for how, q in qs:
        k = query_text(q).casefold()
        if k and k not in seen:
            seen.add(k)
            out.append((how, q))
    return out


def find_release(artist: str, title: str, tracks: list[dict], catno: str | None = None,
                 artists: list[str] | None = None) -> dict:
    """Лучшая папка с релизом. tracks — [{title, duration}] (название трека с микс-версией).
    Останавливаемся на первом шаге, давшем полный релиз во FLAC; иначе пробуем все шаги
    и берём лучшее из найденного."""
    artists = artists or [a for a in re.split(r"\s*(?:,|&)\s*", artist or "") if a]
    if build.words(artist) in {"various artists", "various", "va"}:
        artists = None  # у сборника артисты в путях не обязаны быть
    cands, tried = [], []
    for how, q in release_queries(artist, title, catno):
        files = search(q)
        tried.append({"how": how, "query": query_text(q), "files": len(files)})
        cands += folder_candidates(files, tracks, artists, catno, how, query_text(q))
        if any(c["matched"] == c["total"] and c["quality"] == Q_FLAC for c in cands):
            break
    # мало треков в релизе — то же, что поиск по трекам
    if not any(c["matched"] == c["total"] for c in cands) and len(tracks) == 1:
        for how, q in track_queries(artist, tracks[0]["title"]):
            files = search(q)
            tried.append({"how": how, "query": query_text(q), "files": len(files)})
            cands += folder_candidates(files, tracks, artists, None, how, query_text(q))
            if any(c["quality"] == Q_FLAC for c in cands):
                break
    return result(cands, tried)


def track_queries(artist: str, title: str) -> list[tuple[str, str]]:
    first = re.split(r"\s*(?:,|&| x | feat\.? | ft\.? | vs\.? )\s*", artist or "", flags=re.I)[0]
    no_mix = re.sub(r"\s*[\(\[](?:original|extended|radio)?\s*(?:mix|edit|version)[\)\]]", "", title, flags=re.I)
    qs = [("артист + трек", f"{first} {no_mix}")]
    bare = strip_title(title)
    if bare != no_mix:
        qs.append(("артист + трек без скобок", f"{first} {bare}"))
    return qs


def find_track(artist: str, title: str, duration: int | None, artists: list[str] | None = None) -> dict:
    """Один трек (снятый с Deezer): лучший файл у любого пира."""
    artists = artists or [a for a in re.split(r"\s*(?:,|&)\s*", artist or "") if a]
    cands, tried = [], []
    for how, q in track_queries(artist, title):
        files = search(q)
        tried.append({"how": how, "query": query_text(q), "files": len(files)})
        cands += folder_candidates(files, [{"title": title, "duration": duration}], artists, None, how,
                                   query_text(q))
        if any(c["quality"] == Q_FLAC for c in cands):
            break
    return result(cands, tried)


def result(cands: list[dict], tried: list[dict]) -> dict:
    cands.sort(key=cand_rank, reverse=True)
    # одна и та же папка могла прийти по разным запросам
    seen, uniq = set(), []
    for c in cands:
        if (c["user"], c["dir"]) not in seen:
            seen.add((c["user"], c["dir"]))
            uniq.append(c)
    best = uniq[0] if uniq else None
    status = "none" if not best else "found" if best["matched"] == best["total"] else "partial"
    return {"status": status, "best": best, "alternatives": uniq[1:6], "count": len(uniq), "tried": tried,
            # автоматически отмечать — только полный релиз в FLAC/lossless/MP3 320
            "checkable": bool(best) and status == "found" and best["quality"] >= Q_320}


# ---------- загрузка ----------

DONE_OK = "Completed, Succeeded"


def download(user: str, files: list[dict]) -> None:
    api("POST", f"/transfers/downloads/{requests.utils.quote(user, safe='')}",
        json=[{"filename": f["path"], "size": f["size"]} for f in files])


def transfers(user: str) -> dict[str, dict]:
    """Загрузки от пира: удалённый путь → запись slskd (state, percentComplete, bytesTransferred…)."""
    try:
        d = api("GET", f"/transfers/downloads/{requests.utils.quote(user, safe='')}")
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return {}
        raise
    return {f["filename"]: f for dd in (d or {}).get("directories", []) for f in dd.get("files", [])}


def place_in_queue(user: str, transfer_id: str) -> int | None:
    """Место файла в очереди у пира (slskd спрашивает у пира по запросу)."""
    try:
        v = api("GET", f"/transfers/downloads/{requests.utils.quote(user, safe='')}/{transfer_id}/position")
        return int(v) if isinstance(v, (int, float)) else None
    except (requests.RequestException, ValueError):
        return None


def cancel(user: str, transfer_id: str) -> None:
    try:
        api("DELETE", f"/transfers/downloads/{requests.utils.quote(user, safe='')}/{transfer_id}?remove=true")
    except requests.RequestException:
        pass
