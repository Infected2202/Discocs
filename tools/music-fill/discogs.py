"""Discogs: подтверждение «этот релиз действительно есть у этого артиста».

Deezer склеивает тёзок в одного артиста (чужие синглы, нейрослоп); на Discogs тёзки — разные
артисты («Hermeth», «Hermeth (2)»). Охват шире Beatport (не только электроника), но неполный.
Здесь же — общие правила сравнения названий релизов (title_keys), их использует и beatport.py.

Авторизация — ключ/секрет приложения из config.json (discogs_key / discogs_secret):
60 запросов в минуту. Ответы кэшируются в cache/cache.db (kvcache)."""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import requests

import build
import kvcache

ROOT = Path(__file__).parent
CACHE = ROOT / "cache" / "discogs.json"  # прежний кэш — переносится в cache.db
ARTISTS = ROOT / "cache" / "discogs_artists.json"  # Deezer-артист → выбранный Discogs-артист
TTL = 7 * 86400
API = "https://api.discogs.com"
MAX_PAGES = 20  # 2000 релизов — у самых плодовитых артистов/лейблов дальше хвост из переизданий

_lock = threading.Lock()   # запросы к Discogs — строго по одному (лимит на минуту общий)
_io = threading.Lock()


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


_cache = kvcache.Store("discogs", CACHE)
_session = requests.Session()


def _auth() -> dict:
    cfg = _read(ROOT / "config.json", {})
    if not cfg.get("discogs_key"):
        raise RuntimeError('нет ключа Discogs — добавь "discogs_key"/"discogs_secret" в config.json')
    return {"User-Agent": "music-fill/0.1 (local library tool)",
            "Authorization": f"Discogs key={cfg['discogs_key']}, secret={cfg['discogs_secret']}"}


def get(path: str, **params) -> dict:
    url = f"{API}{path}?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    with _lock:
        for attempt in range(6):
            r = _session.get(f"{API}{path}", params=params, headers=_auth(), timeout=30)
            if r.status_code == 429:  # лимит: ждём, пока окно в минуту освободится
                time.sleep(5 + 5 * attempt)
                continue
            break
        if int(r.headers.get("X-Discogs-Ratelimit-Remaining", 60)) <= 3:
            time.sleep(3)
        if r.status_code == 404:
            data = {}
        else:
            r.raise_for_status()
            data = r.json()
    _cache.put(url, data)
    return data


def paged(path: str, key: str) -> list[dict]:
    out = []
    for page in range(1, MAX_PAGES + 1):
        d = get(path, per_page=100, page=page)
        out += d.get(key, [])
        if page >= (d.get("pagination") or {}).get("pages", 1):
            break
    return out


# ---------- названия ----------

def clean_name(name: str) -> str:
    """'Hermeth (2)' → 'Hermeth'; 'Nina Kraviz*' (вариант написания) → 'Nina Kraviz'."""
    return re.sub(r"\s*\(\d+\)$", "", (name or "").strip()).rstrip("*").strip()


VARIOUS = {"various", "various artists", "va"}


def is_various(name: str) -> bool:
    return clean_name(name).lower() in VARIOUS


def title_parts(title: str) -> list[str]:
    """Discogs пишет двуязычные названия через ' = ': 'Резонанс = Resonance' → оба варианта."""
    return [p.strip() for p in (title or "").split(" = ") if p.strip()]


def _no_yj(key: str) -> str:
    """Ключ для сравнения разных транслитов (я → ya/ja, ъ → ''/y). Только для длинных названий:
    у коротких ('Joy' / 'Oy') без y/j остаётся слишком мало, чтобы отличать."""
    k = re.sub(r"[yj]", "", key)
    return "~" + k if len(k) >= 8 and k != key else ""


ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}


def title_keys(title: str) -> set[str]:
    """Ключи сравнения названий релизов: точное (без регистра/пунктуации) и без скобок
    ('Mixmag Presents X (DJ Mix)' ~ 'Mixmag Presents: X'); у обоих — без хвоста EP/LP/Single
    и в транслите ('Странно-Странно. Необъятно' ~ 'Stranno Stranno Neobjatno').
    Только равенство ключей — никаких «похоже» и «начинается с»."""
    # ремиксовый релиз сравнивается только с ремиксовым: скобки отбрасываются, и без этой метки
    # 'X (Y Remix)' сошёлся бы с оригинальным 'X'. Внутри метки место слова не важно:
    # 'El Baile Aleman Remixes Part 1' ~ 'El Baile Alemán, Pt. 1 (Remixes)'
    remix = bool(re.search(r"\bremix", build.words(title)))
    keys = set()
    for part in title_parts(title):
        for k in (build.words(part), build.norm_album(part)):
            if remix:
                # 'Remixed' целиком: без слова remix ничего не остаётся — ключом становится само слово
                k = re.sub(r"\s+", " ", re.sub(r"\bremix(?:es|ed)?\b", " ", k)).strip() or "remixes"
            k = re.sub(r"\bpart\b", "pt", k)  # 'Remixes Part 1' (Beatport) ~ 'Remixes, Pt. 1' (Deezer)
            # 'Part II' ~ 'Pt. 2', 'Vol. VI' ~ 'Vol. 6' — римские цифры только после pt/vol
            k = re.sub(r"\b(pt|vol)\s+(x|ix|viii|vii|vi|v|iv|iii|ii|i)\b",
                       lambda m: f"{m.group(1)} {ROMAN[m.group(2)]}", k)
            # 'Post Raw Era III' ~ 'Post Raw Era, Pt. 3': римская в конце; 'i' — только после 3+ слов
            m = re.search(r"\s(x|ix|viii|vii|vi|v|iv|iii|ii)$", k) or \
                (re.search(r"\s(i)$", k) if len(k.split()) >= 4 else None)
            ends = [k[:m.start()] + f" pt {ROMAN[m.group(1)]}", k[:m.start()] + f" {ROMAN[m.group(1)]}"] if m else []
            for v in [k, re.sub(r"\s+(?:mini lp|mini album|ep|lp|single|album)$", "", k)] + ends:
                keys.add(v)
                tr = v.translate(build.CYR_LAT)
                keys.add(tr)
                if tr != v:  # транслит у всех свой: 'необъятно' → neobjatno / neobyatno
                    keys.add(_no_yj(tr))
    keys |= {_no_yj(k) for k in keys if k.isascii()}
    keys.discard("")
    return {f"remix:{k}" for k in keys} if remix else keys


def same_artist(a: str, b: str) -> bool:
    a, b = clean_name(a), clean_name(b)
    return build.norm(a) == build.norm(b) or build.artists_similar(a, b)


# ---------- артисты ----------

def search(q: str, type_: str, limit: int = 10) -> list[dict]:
    return get("/database/search", q=q, type=type_, per_page=limit).get("results", [])


def artist_releases(artist_id: int) -> list[dict]:
    """Все релизы Discogs-артиста: свои (Main), участие в сборниках, ремиксы и т.д."""
    return paged(f"/artists/{artist_id}/releases", "releases")


def release_url(r: dict) -> str:
    return f"https://www.discogs.com/{'master' if r.get('type') == 'master' else 'release'}/{r['id']}"


def release_index(releases: list[dict]) -> dict[str, dict]:
    idx = {}
    for r in releases:
        for k in title_keys(r.get("title", "")):
            idx.setdefault(k, r)
    return idx


def match_artist(deezer_id, name: str, titles: list[str]) -> dict | None:
    """Discogs-артист для Deezer-артиста. Кандидаты — тёзки по имени (у Discogs они с номером:
    'Hermeth (2)'); выбираем того, у кого больше всего релизов совпадает с дискографией Deezer.
    Запоминаем выбор — повторно не перебираем."""
    with _io:
        stored = _read(ARTISTS, {}).get(str(deezer_id))
    if stored and time.time() - stored["ts"] < TTL:
        return stored["artist"]
    want = [title_keys(t) for t in titles]
    best, best_score = None, 0
    for c in search(name, "artist", 15):
        if not same_artist(c.get("title", ""), name):
            continue
        idx = release_index(artist_releases(c["id"]))
        score = sum(1 for keys in want if keys & idx.keys())
        if score > best_score:
            best, best_score = c, score
    artist = {"id": best["id"], "name": best["title"], "score": best_score,
              "url": f"https://www.discogs.com/artist/{best['id']}"} if best else None
    with _io:
        data = _read(ARTISTS, {})
        data[str(deezer_id)] = {"ts": time.time(), "artist": artist}
        _write(ARTISTS, data)
    return artist
