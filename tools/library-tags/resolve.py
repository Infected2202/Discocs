"""Тип релиза для альбомов без штрихкода (старые рипы, чарты Beatport, раздачи Soulseek): поиск + строгая сверка.

    python resolve.py                 # пробный прогон: файлы не меняет — отчёт out/resolve.txt / resolve.json
    python resolve.py --write         # записать подтверждённое (журнал — тот же out/apply_log.jsonl, откат — apply.py --undo)

Берёт из выгрузки Navidrome (../music-fill/raw/navidrome) альбомы без типа. Для каждого:
1. Deezer — поиск по исполнителю и названию альбома, до 5 кандидатов;
2. Discogs — по каталожному номеру из тегов (наши загрузки из Soulseek), иначе поиск по исполнителю и названию.
Кандидат принимается, только если совпали название альбома, исполнитель (кроме сборников) и КАЖДЫЙ трек альбома —
(название может отличаться написанием — сходство от 80 %, но тогда длительность ±2 с) —
по названию и длительности ±3 с (у Discogs длительность бывает не указана — тогда только название, но трек-лист
должен совпасть целиком). Тип у Discogs — из описания формата (Album/LP, EP, Single, Compilation); если формат
не говорит явно — тип не ставится. Несколько подходящих кандидатов с разным типом — ничего не ставится.
"""
from __future__ import annotations

import collections
import difflib
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import mutagen
import requests

from dry_run import OUT, ROOT, norm, read_json, write_json

sys.path.insert(0, str(ROOT.parent / "music-fill"))
import discogs  # noqa: E402  — клиент Discogs из music-fill (ключ, кэш, лимиты)

ND = ROOT.parent / "music-fill" / "raw" / "navidrome"
LIB = Path(r"H:\data\media\music")
PATHS = ROOT / "cache" / "paths.json"
SEARCH = ROOT / "cache" / "deezer_search.json"
DUR_TOL = 3
VA = {"various artists", "various", "va"}
DZ_TYPES = {"album": "album", "ep": "ep", "single": "single", "compile": "compilation"}

# ---------- Deezer с общим лимитом на все потоки ----------
_rl = threading.Lock()
_last = [0.0]
_cache_lock = threading.Lock()
_cache: dict = {}


def deezer(path: str) -> dict:
    with _cache_lock:
        if path in _cache:
            return _cache[path]
    for _ in range(5):
        with _rl:  # квота 50 запросов / 5 с
            wait = 0.11 - (time.time() - _last[0])
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        try:
            j = requests.get("https://api.deezer.com/" + path, timeout=30).json()
        except (requests.RequestException, ValueError):
            time.sleep(3)
            continue
        if (j.get("error") or {}).get("code") == 4:
            time.sleep(5)
            continue
        if path.startswith("album/") and "error" not in j:
            data = (j.get("tracks") or {}).get("data") or []
            if (j.get("nb_tracks") or 0) > len(data):
                more = deezer(f"album/{j['id']}/tracks?limit=1000")
                if more.get("data"):
                    j["tracks"]["data"] = more["data"]
        with _cache_lock:
            _cache[path] = j
        return j
    return {"error": {"message": "не ответил"}}


# ---------- файлы ----------

def path_index() -> dict[int, list[str]]:
    """Размер файла -> пути (Navidrome отдаёт виртуальные пути, настоящие сопоставляем по размеру)."""
    idx = read_json(PATHS, None)
    if idx is None:
        def walk(top):
            out = []
            for dp, _ds, fs in os.walk(top):
                for f in fs:
                    p = os.path.join(dp, f)
                    if f.lower().endswith((".flac", ".mp3", ".m4a", ".opus", ".ogg", ".wma", ".ape", ".aiff", ".wav")):
                        out.append((p, os.path.getsize(p)))
            return out
        tops = [str(LIB / d) for d in os.listdir(LIB) if (LIB / d).is_dir()]
        with ThreadPoolExecutor(8) as ex:
            pairs = [x for part in ex.map(walk, tops) for x in part]
        idx = collections.defaultdict(list)
        for p, s in pairs:
            idx[str(s)].append(p)
        write_json(PATHS, idx)
    return idx


def catno_of(path: str) -> tuple[str | None, str | None]:
    try:
        t = mutagen.File(path).tags or {}
    except Exception:
        return None, None
    d = {}
    for k, v in t.items():
        k2 = k.lower()[5:] if k.lower().startswith("txxx:") else k.lower()
        d[k2] = str(v[0] if isinstance(v, list) and v else v)
    label = d.get("publisher") or d.get("label") or d.get("organization") or d.get("tpub")
    return d.get("catalognumber") or d.get("catalog"), label


# ---------- сверка ----------

def title_norm(s: str | None) -> str:
    s = norm(s)
    return re.sub(r"\s+(ep|lp|e p)$", "", s)


def dur(s) -> float | None:
    if isinstance(s, (int, float)):
        return float(s)
    m = re.fullmatch(r"(?:(\d+):)?(\d+):(\d\d)", (s or "").strip())
    if not m:
        return None
    h, mi, se = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(se)


def tracks_match(songs: list[dict], cand: list[dict], need_dur: bool) -> bool:
    """Каждая песня альбома — в треклисте кандидата (название + длительность ±3 с)."""
    used = set()
    for s in songs:
        hit = None
        for i, c in enumerate(cand):
            if i in used:
                continue
            if norm(s["title"]) not in c["names"]:
                # «A Staya» / «a- steya», «Brahmachary» / «brahmacharya»: похожее название — только вместе с
                # длительностью ±2 с
                close = c["dur"] is not None and abs(c["dur"] - (s.get("duration") or 0)) <= 2 and max(
                    difflib.SequenceMatcher(None, norm(s["title"]), n).ratio() for n in c["names"]) >= 0.8
                if not close:
                    continue
            if c["dur"] is not None:
                if abs(c["dur"] - (s.get("duration") or 0)) > DUR_TOL:
                    continue
            elif need_dur:
                continue
            hit = i
            break
        if hit is None:
            return False
        used.add(hit)
    return True


def same_artist(nd: str, cand: str) -> bool:
    a, b = norm(nd), norm(cand)
    return a in VA or a == b or (a and b and (a in b or b in a))


def via_deezer(alb: dict, songs: list[dict]) -> list[dict]:
    artist = alb.get("artist") or ""
    q = f'album:"{alb["name"]}"' if norm(artist) in VA else f'artist:"{artist}" album:"{alb["name"]}"'
    res = deezer("search/album?q=" + requests.utils.quote(q) + "&limit=5")
    out = []
    for c in res.get("data", [])[:5]:
        if title_norm(c.get("title")) != title_norm(alb["name"]):
            continue
        if not same_artist(artist, (c.get("artist") or {}).get("name", "")):
            continue
        a = deezer(f"album/{c['id']}")
        if "error" in a:
            continue
        cand = [{"names": {norm(d.get("title")), norm(d.get("title_short")),
                           norm(f"{d.get('title_short', '')} {d.get('title_version', '')}")},
                 "dur": d.get("duration"), "id": d.get("id")} for d in (a.get("tracks") or {}).get("data", [])]
        if tracks_match(songs, cand, need_dur=True):
            out.append({"source": "deezer", "id": a["id"], "title": a.get("title"),
                        "type": DZ_TYPES.get(a.get("record_type"), a.get("record_type")),
                        "nb_tracks": a.get("nb_tracks"), "label": a.get("label")})
    return out


def discogs_type(rel: dict) -> str | None:
    desc = {d.lower() for f in rel.get("formats") or [] for d in (f.get("descriptions") or [])}
    if "compilation" in desc:
        return "compilation"
    if desc & {"album", "lp", "mini-album"}:
        return "album"
    if "ep" in desc:
        return "ep"
    if desc & {"single", "maxi-single"}:
        return "single"
    return None


def via_discogs(alb: dict, songs: list[dict], catno: str | None, label: str | None) -> list[dict]:
    artist = alb.get("artist") or ""
    if catno:
        hits = discogs.get("/database/search", type="release", catno=catno, per_page=10).get("results", [])
    else:
        params = {"type": "release", "release_title": alb["name"], "per_page": 10}
        if norm(artist) not in VA:
            params["artist"] = artist
        hits = discogs.get("/database/search", **params).get("results", [])
    out, seen = [], set()
    for h in hits[:6]:
        if h.get("id") in seen:
            continue
        seen.add(h.get("id"))
        rel = discogs.get(f"/releases/{h['id']}")
        if not rel or title_norm(rel.get("title")) != title_norm(alb["name"]):
            continue
        if not same_artist(artist, " ".join(a.get("name", "") for a in rel.get("artists") or [])):
            continue
        cand = [{"names": {norm(t.get("title"))}, "dur": dur(t.get("duration"))}
                for t in rel.get("tracklist") or [] if t.get("type_", "track") == "track"]
        if not tracks_match(songs, cand, need_dur=False):
            continue
        t = discogs_type(rel)
        if t:
            out.append({"source": "discogs", "id": rel["id"], "title": rel.get("title"), "type": t,
                        "nb_tracks": len(cand), "label": ", ".join(l.get("name", "") for l in rel.get("labels") or [])})
    return out


def resolve_one(alb: dict, songs: list[dict], paths: list[str]) -> dict:
    row = {"album_id": alb["id"], "album": alb["name"], "artist": alb.get("artist"), "songs": len(songs),
           "paths": paths, "dir": os.path.dirname(paths[0]) if paths else None}
    if len(paths) != len(songs):
        row["status"] = "files_unknown"  # не все файлы нашлись по размеру
        return row
    catno, label = catno_of(paths[0])
    cands = via_deezer(alb, songs)
    if not cands:
        cands = via_discogs(alb, songs, catno, label)
        if not cands and catno:
            cands = via_discogs(alb, songs, None, label)
    types = {c["type"] for c in cands if c.get("type")}
    row["candidates"] = cands
    row["status"] = "ok" if len(types) == 1 else "conflict" if len(types) > 1 else "not_found"
    if row["status"] == "ok":
        row["type"] = types.pop()
        row["source"] = cands[0]["source"]
        row["source_id"] = cands[0]["id"]
    return row


def main() -> None:
    write = "--write" in sys.argv
    if write:
        return write_types()
    global _cache
    _cache = read_json(SEARCH, {})
    albums = [a for a in read_json(ND / "albums.json", []) if not a.get("releaseTypes")]
    if "--limit" in sys.argv:  # проба на части альбомов (случайная выборка)
        import random
        albums = random.Random(1).sample(albums, int(sys.argv[sys.argv.index("--limit") + 1]))
    by_album = collections.defaultdict(list)
    for s in read_json(ND / "songs.json", []):
        by_album[s["albumId"]].append(s)
    idx = path_index()
    print(f"альбомов без типа: {len(albums)}", flush=True)
    rows, lock, t0 = [], threading.Lock(), time.time()

    def one(a):
        songs = by_album[a["id"]]
        paths = [idx[str(s["size"])][0] for s in songs if len(idx.get(str(s["size"]), [])) == 1]
        try:
            r = resolve_one(a, songs, paths)
        except Exception as e:
            r = {"album": a["name"], "status": "error", "error": f"{type(e).__name__}: {e}"}
        with lock:
            rows.append(r)
            if len(rows) % 200 == 0:
                print(f"  {len(rows)}/{len(albums)} ({time.time() - t0:.0f} с)", flush=True)
                with _cache_lock:
                    write_json(SEARCH, _cache)

    with ThreadPoolExecutor(6) as ex:
        list(ex.map(one, albums))
    with _cache_lock:
        write_json(SEARCH, _cache)
    discogs.flush()
    write_json(OUT / "resolve.json", rows)
    report(rows)


def report(rows: list[dict]) -> None:
    st = collections.Counter(r["status"] for r in rows)
    ok = [r for r in rows if r["status"] == "ok"]
    root = lambda r: (r.get("dir") or "").replace(str(LIB) + "\\", "").split("\\")[0] or "?"
    by_root = collections.defaultdict(collections.Counter)
    for r in rows:
        by_root[root(r)][r["status"]] += 1
    lines = [f"альбомов: {len(rows)}; " + ", ".join(f"{k} {v}" for k, v in st.most_common()),
             "по источнику: " + ", ".join(f"{k} {v}" for k, v in collections.Counter(r["source"] for r in ok).items()),
             "тип: " + ", ".join(f"{k} {v}" for k, v in collections.Counter(r["type"] for r in ok).most_common()),
             "по папкам: " + "; ".join(f"{k}: {dict(v)}" for k, v in by_root.items()), ""]
    lines.append("== найдено")
    lines += [f"  {r['type']:<11} {r['source']:<7} | {r['artist']} — {r['album']} | {root(r)} | {r['candidates'][0]['title']}"
              for r in sorted(ok, key=lambda r: (root(r), r["album"]))]
    lines.append("\n== разные типы у кандидатов")
    lines += [f"  {r['artist']} — {r['album']} | " + "; ".join(f"{c['source']} {c['type']}" for c in r["candidates"])
              for r in rows if r["status"] == "conflict"]
    (OUT / "resolve.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:4]))
    print("отчёт:", OUT / "resolve.txt")


def write_types() -> None:
    """Запись подтверждённого: RELEASETYPE (если ещё нет) + ID источника; журнал — out/apply_log.jsonl."""
    from apply import apply as apply_items, read_fields  # noqa: F401
    import apply as ap
    rows = [r for r in read_json(OUT / "resolve.json", []) if r["status"] == "ok"]
    scan = read_json(ap.SCAN, {})
    items, skipped = [], 0
    for r in rows:
        if any(Path(p).suffix.lower() not in (".flac", ".mp3") for p in r["paths"]):
            skipped += 1  # пишем только FLAC/MP3
            continue
        for p in r["paths"]:
            st = os.stat(p)
            scan[p] = {**scan.get(p, {}), "_size": st.st_size, "_mtime": int(st.st_mtime)}
            vals = {"RELEASETYPE": r["type"]}
            vals["DEEZER_ALBUM_ID" if r["source"] == "deezer" else "DISCOGS_RELEASE_ID"] = str(r["source_id"])
            items.append((p, vals))
    write_json(ap.SCAN, scan)
    print(f"релизов: {len(rows) - skipped} (не FLAC/MP3 — пропущено {skipped}), файлов: {len(items)}")
    print(dict(apply_items(items)))


if __name__ == "__main__":
    main()
