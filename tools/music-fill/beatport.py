"""Beatport API v4: полные каталоги лейблов и артистов электроники, у каждого релиза — UPC.

Вход — своим аккаунтом (python beatport_login.py), в config.json лежат только токены;
access продлевается здесь refresh-токеном. Ответы кэшируются в cache/cache.db (kvcache).
UPC Beatport = UPC Deezer (album/upc:…) — релиз сопоставляется точно, без поиска по названию."""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import requests

import build
import discogs  # title_keys / same_artist — общие правила сравнения названий
import kvcache

ROOT = Path(__file__).parent
CONFIG = ROOT / "config.json"
CACHE = ROOT / "cache" / "beatport.json"  # прежний кэш — переносится в cache.db
ARTISTS = ROOT / "cache" / "beatport_artists.json"  # Deezer-артист → выбранный Beatport-артист
TTL = 7 * 86400
API = "https://api.beatport.com/v4"
CLIENT_ID = "0GIvkCltVIuPkkwSJHp6NDb3s0potTjLBQr388Dd"  # клиент страницы документации API
MAX_PAGES = 30
VARIOUS_MIN = 4  # столько артистов и больше — это сборник (Beatport не пишет Various Artists)

_lock = threading.Lock()
_io = threading.Lock()
_session = requests.Session()
_session.headers["User-Agent"] = "music-fill/0.1 (local library tool)"


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


_cache = kvcache.Store("beatport", CACHE)


# ---------- токен ----------

def _token(force_refresh: bool = False) -> str:
    cfg = _read(CONFIG, {})
    tok = cfg.get("beatport_token")
    if not tok:
        raise RuntimeError("нет входа в Beatport — запусти python beatport_login.py")
    if force_refresh or time.time() > tok["expires_at"]:
        r = _session.post(f"{API}/auth/o/token/", data={"grant_type": "refresh_token", "client_id": CLIENT_ID,
                                                       "refresh_token": tok.get("refresh_token")})
        if r.status_code != 200:
            raise RuntimeError("вход в Beatport истёк — запусти python beatport_login.py")
        new = r.json()
        tok = {"access_token": new["access_token"], "refresh_token": new.get("refresh_token", tok.get("refresh_token")),
               "expires_at": time.time() + new.get("expires_in", 36000) - 60}
        with _io:
            cfg = _read(CONFIG, {})
            cfg["beatport_token"] = tok
            CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return tok["access_token"]


def get(path: str, **params) -> dict:
    url = f"{path}?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    with _lock:  # по одному запросу: лимитов Beatport не публикует — не наглеем
        refreshed = False
        for attempt in range(6):
            r = _session.get(f"{API}{path}", params=params, timeout=30,
                             headers={"Authorization": f"Bearer {_token(force_refresh=refreshed)}"})
            if r.status_code == 401 and not refreshed:
                refreshed = True
                continue
            if r.status_code == 429:
                time.sleep(5 + 5 * attempt)
                continue
            break
        time.sleep(0.2)
        if r.status_code == 404:
            data = {}
        else:
            r.raise_for_status()
            data = r.json()
    _cache.put(url, data)
    return data


def paged(path: str, **params) -> list[dict]:
    out = []
    for page in range(1, MAX_PAGES + 1):
        d = get(path, per_page=100, page=page, **params)
        out += d.get("results", [])
        if not d.get("next"):
            break
    return out


# ---------- релизы ----------

def release_url(r: dict) -> str:
    return f"https://www.beatport.com/release/{r.get('slug') or 'x'}/{r['id']}"


def artists_of(r: dict) -> list[str]:
    return [a["name"] for a in r.get("artists", [])]


def is_various(r: dict) -> bool:
    return len(r.get("artists", [])) >= VARIOUS_MIN


def display_artist(r: dict) -> str:
    names = artists_of(r)
    return "Various Artists" if is_various(r) else ", ".join(names)


def upc(value) -> str:
    """UPC бывает с ведущими нулями и без ('0723277340913' / '723277340913')."""
    return str(value or "").strip().lstrip("0")


def search(q: str, type_: str, limit: int = 10) -> list[dict]:
    return get("/catalog/search/", q=q, type=type_, per_page=limit).get(type_, [])


def artist_releases(artist_id: int) -> list[dict]:
    """Все релизы Beatport-артиста: свои, сборники, ремиксы."""
    return paged("/catalog/releases/", artist_id=artist_id)


def match_artist(deezer_id, name: str, deezer_releases: list[dict]) -> dict | None:
    """Beatport-артист для Deezer-артиста. У Beatport тёзки — с одинаковым именем, поэтому из
    кандидатов берём того, у кого больше всего релизов совпадает с дискографией Deezer
    (по UPC или по названию). deezer_releases: [{title, upc}]."""
    with _io:
        stored = _read(ARTISTS, {}).get(str(deezer_id))
    if stored and time.time() - stored["ts"] < TTL:
        return stored["artist"]
    best, best_score = None, 0
    for c in search(name, "artists", 10):
        if not discogs.same_artist(c.get("name", ""), name):
            continue
        hits = confirm(deezer_releases, artist_releases(c["id"]))
        if len(hits) > best_score:
            best, best_score = c, len(hits)
    artist = {"id": best["id"], "name": best["name"], "score": best_score,
              "url": f"https://www.beatport.com/artist/{best.get('slug') or 'x'}/{best['id']}"} if best else None
    with _io:
        data = _read(ARTISTS, {})
        data[str(deezer_id)] = {"ts": time.time(), "artist": artist}
        _write(ARTISTS, data)
    return artist


def confirm(deezer_releases: list[dict], bp_releases: list[dict]) -> dict:
    """Какие релизы Deezer есть в списке Beatport: сначала по UPC, иначе по названию
    (discogs.title_keys — только равенство ключей). → {deezer id: beatport url}."""
    by_upc = {upc(r.get("upc")): r for r in bp_releases if upc(r.get("upc"))}
    by_title = {}
    for r in bp_releases:
        for k in discogs.title_keys(r.get("name", "")):
            by_title.setdefault(k, r)
    out = {}
    for d in deezer_releases:
        hit = by_upc.get(upc(d.get("upc"))) if upc(d.get("upc")) else None
        hit = hit or next((by_title[k] for k in discogs.title_keys(d["title"]) if k in by_title), None)
        if hit:
            out[str(d["id"])] = release_url(hit)
    return out


# ---------- лейблы ----------

def parse_label_query(q: str) -> tuple[int | None, str]:
    """Ссылка Beatport (…/label/podvodo-records/77039) → id; иначе — строка поиска."""
    m = re.search(r"beatport\.com/(?:[a-z]{2}/)?label/[^/]+/(\d+)", q)
    if m:
        return int(m.group(1)), ""
    return None, q.strip()


def label(label_id: int) -> dict:
    return get(f"/catalog/labels/{label_id}/")


def search_labels(q: str) -> list[dict]:
    lid, text = parse_label_query(q)
    found = [label(lid)] if lid else search(text, "labels", 15)
    return [{"id": l["id"], "name": l["name"], "thumb": (l.get("image") or {}).get("uri", ""),
             "url": f"https://www.beatport.com/label/{l.get('slug') or 'x'}/{l['id']}"} for l in found if l]


def label_releases(label_id: int) -> list[dict]:
    return paged(f"/catalog/labels/{label_id}/releases/")


def guess_type(r: dict) -> str:
    """Тип релиза Beatport не отдаёт: сборник — много артистов, EP/сингл — по названию и числу треков."""
    if is_various(r):
        return "compile"
    name = build.words(r.get("name", ""))
    if re.search(r"\bep\b", name):
        return "ep"
    n = r.get("track_count") or 0
    return "single" if n <= 3 else "ep" if n <= 6 else "album"
