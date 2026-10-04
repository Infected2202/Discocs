"""Популярность Deezer — в базу discocs: какой трек Navidrome = какой трек/альбом Deezer, плюс rank/fans.

    python popularity.py            # собрать out/popularity.json
    python popularity.py --push     # и загрузить в discocs (ssh homelab → recs deezer-import)

ID Deezer лежат в тегах файлов (DEEZER_ALBUM_ID / DEEZER_TRACK_ID — их записали apply.py, resolve.py и
autotag.py, всё есть в out/apply_log.jsonl), а Navidrome свои теги наружу не отдаёт — поэтому связь
собирается здесь: реальный путь файла ↔ путь песни в API Navidrome (родной /api/song, там путь от корня
библиотеки, не виртуальный, как в Subsonic). rank/fans — из cache/deezer_albums.json, чтобы discocs не
спрашивал заново; дальше он сам освежает их раз в неделю (app/popularity.py).

Повторный запуск безопасен: discocs обновляет связи и не затирает более свежие снимки. Новые загрузки
попадают сюда, когда discocs уже знает их трек (после navidrome-sync).
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

from dry_run import ALBUMS, OUT, read_json, write_json
from navidrome import CFG

LIBRARY = Path(r"H:\data\media\music")  # корень библиотеки Navidrome на этой машине
PUSH = ["ssh", "homelab", "docker exec -i -w /app discocs-backend-1 recs deezer-import -"]


def written_ids() -> dict[str, dict]:
    """Реальный путь → {album, track}: последнее записанное в журнал apply."""
    out: dict[str, dict] = {}
    with (OUT / "apply_log.jsonl").open(encoding="utf-8") as log:
        for line in log:
            try:
                e = json.loads(line)
            except ValueError:
                continue  # строка, оборванная параллельной записью
            ids = out.setdefault(e["path"], {})
            after = e.get("after") or {}
            if after.get("DEEZER_ALBUM_ID"):
                if ids.get("album") != after["DEEZER_ALBUM_ID"]:
                    ids.pop("track", None)  # трек от другого альбома не годится
                ids["album"] = after["DEEZER_ALBUM_ID"]
            if after.get("DEEZER_TRACK_ID"):
                ids["track"] = after["DEEZER_TRACK_ID"]
    return {p: ids for p, ids in out.items() if ids.get("album")}


def navidrome_songs() -> dict[str, str]:
    """Путь (нижний регистр, от корня библиотеки) → ID песни в Navidrome."""
    cfg = read_json(CFG, {})
    base = cfg["navidrome_url"].rstrip("/")
    r = requests.post(base + "/auth/login", timeout=30,
                      json={"username": cfg["navidrome_user"], "password": cfg["navidrome_password"]})
    r.raise_for_status()
    headers = {"x-nd-authorization": f"Bearer {r.json()['token']}"}
    # Одним запросом: постранично (_start/_end) Navidrome 0.64 отдаёт страницы внахлёст — из 79 тыс. песен
    # уникальных выходило 57 тыс., а _sort=id на дальних страницах падает («ambiguous column name»).
    total = int(requests.get(base + "/api/song", headers=headers, timeout=60,
                             params={"_start": 0, "_end": 1}).headers.get("x-total-count") or 0)
    r = requests.get(base + "/api/song", headers=headers, timeout=600,
                     params={"_start": 0, "_end": total + 1000, "_sort": "media_file.id"})
    r.raise_for_status()
    songs = r.json()
    if len({s["id"] for s in songs}) < total:
        raise SystemExit(f"Navidrome отдал {len(songs)} песен из {total}")
    print(f"  Navidrome: {len(songs)} песен", flush=True)
    return {s["path"].replace("\\", "/").lower(): s["id"] for s in songs if not s.get("missing")}


def deezer_snapshots() -> tuple[dict[str, dict], dict[str, int]]:
    """Из кэша ответов Deezer: альбом → {fans, fetched_at}, трек → rank."""
    cache = read_json(ALBUMS, {})
    fetched_at = datetime.fromtimestamp(ALBUMS.stat().st_mtime, timezone.utc).isoformat()
    albums, ranks = {}, {}
    for a in cache.values():
        if not isinstance(a, dict) or "id" not in a:
            continue
        albums[str(a["id"])] = {"fans": a.get("fans"), "fetched_at": fetched_at}
        for t in (a.get("tracks") or {}).get("data") or []:
            if t.get("rank") is not None:
                ranks[str(t["id"])] = t["rank"]
    return albums, ranks


def main() -> None:
    ids = written_ids()
    print(f"файлов с ID Deezer: {len(ids)}")
    songs = navidrome_songs()
    albums, ranks = deezer_snapshots()
    tracks, unknown = [], 0
    for path, x in ids.items():
        try:
            rel = Path(path).relative_to(LIBRARY).as_posix().lower()
        except ValueError:
            unknown += 1
            continue
        nd = songs.get(rel)
        if nd is None:
            unknown += 1  # файл скрыт .ndignore, удалён или переименован
            continue
        tracks.append({"navidrome_id": nd, "deezer_album_id": int(x["album"]),
                       "deezer_track_id": int(x["track"]) if x.get("track") else None,
                       "rank": ranks.get(x.get("track") or "")})
    used = {str(t["deezer_album_id"]) for t in tracks}
    payload = {"tracks": tracks,
               "albums": [{"deezer_album_id": int(a), **albums[a]} for a in sorted(used) if a in albums]}
    write_json(OUT / "popularity.json", payload)
    print(f"треков: {len(tracks)} (не нашлись в Navidrome: {unknown}), альбомов: {len(used)}, "
          f"со снимком fans: {len(payload['albums'])}, с rank: {sum(t['rank'] is not None for t in tracks)}")
    print("файл:", OUT / "popularity.json")
    if "--push" in sys.argv:
        res = subprocess.run(PUSH, input=json.dumps(payload).encode(), capture_output=True, timeout=600)
        print(res.stdout.decode(errors="replace").strip(), res.stderr.decode(errors="replace").strip()[-2000:])
        if res.returncode:
            sys.exit(res.returncode)


if __name__ == "__main__":
    main()
