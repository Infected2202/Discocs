"""Запись в теги по результатам dry_run.py: тип релиза и ID Deezer.

    python apply.py --sample 20         # пробно: 20 случайных релизов разных типов
    python apply.py --barcode 0617465007552
    python apply.py --all               # все подтверждённые релизы
    python apply.py --undo              # откатить всё записанное (по журналу)

Пишет только подтверждённые релизы (каждый файл найден в треклисте альбома Deezer):
- RELEASETYPE (album / ep / single / compilation) — если ни у одного файла релиза типа ещё нет
  (существующий не перезаписывается: он из MusicBrainz, точнее);
- DEEZER_ALBUM_ID, DEEZER_TRACK_ID.
FLAC — Vorbis comment, MP3 — TXXX (версия ID3 сохраняется). Файл, изменившийся после dry_run, пропускается.
Каждая запись — строка в out/apply_log.jsonl: путь, что было до и что записано (для --undo).
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.id3 import ID3, TXXX

from dry_run import OUT, SCAN, read_json

LOG = OUT / "apply_log.jsonl"
FIELDS = ("RELEASETYPE", "DEEZER_ALBUM_ID", "DEEZER_TRACK_ID")
WRITABLE = ("ok", "ok_loose")


def read_fields(p: Path) -> dict:
    if p.suffix.lower() == ".flac":
        t = FLAC(p).tags or {}
        return {k: (t.get(k) or [None])[0] for k in FIELDS}
    t = ID3(p)
    return {k: (t[f"TXXX:{k}"].text[0] if f"TXXX:{k}" in t else None) for k in FIELDS}


def write_fields(p: Path, values: dict) -> None:
    """values: поле -> строка (записать) или None (удалить)."""
    if p.suffix.lower() == ".flac":
        f = FLAC(p)
        if f.tags is None:
            f.add_tags()
        for k, v in values.items():
            if v is None:
                f.tags.pop(k, None)
            else:
                f.tags[k] = [v]
        f.save()
        return
    t = ID3(p)
    for k, v in values.items():
        t.delall(f"TXXX:{k}")
        if v is not None:
            t.add(TXXX(encoding=3, desc=k, text=[v]))
    t.save(p, v2_version=t.version[1] if t.version[1] in (3, 4) else 3)


def plan(releases: list[dict]) -> list[tuple[str, dict]]:
    """[(путь, {поле: значение})] для подтверждённых релизов."""
    out = []
    for r in releases:
        if r["status"] not in WRITABLE:
            continue
        typed = bool(r["releasetype_tag"])
        for t in r["tracks"]:
            vals = {"DEEZER_ALBUM_ID": str(r["deezer_album_id"]), "DEEZER_TRACK_ID": str(t["deezer_track_id"])}
            if not typed and r.get("record_type"):
                vals["RELEASETYPE"] = r["record_type"]
            out.append((t["path"], vals))
    return out


def pick_sample(releases: list[dict], n: int) -> list[dict]:
    """Разные случаи: по типам, FLAC/MP3, нестрогая сверка, переиздание по ISRC."""
    ok = [r for r in releases if r["status"] in WRITABLE]
    rnd = random.Random(42)
    buckets = collections.defaultdict(list)
    for r in ok:
        ext = Path(r["tracks"][0]["path"]).suffix.lower()
        kind = "isrc" if r["via_isrc"] else "loose" if r["status"] == "ok_loose" else r["record_type"]
        buckets[(kind, ext)].append(r)
    picked = []
    keys = sorted(buckets)
    while len(picked) < n and any(buckets.values()):
        for k in keys:
            if buckets[k] and len(picked) < n:
                picked.append(buckets[k].pop(rnd.randrange(len(buckets[k]))))
    return picked


def apply(items: list[tuple[str, dict]], workers: int = 8) -> collections.Counter:
    """Библиотека на SMB-шаре: упор в сетевые задержки, а не в диск, поэтому файлы — параллельно."""
    scan = read_json(SCAN, {})
    st = collections.Counter()
    lock = threading.Lock()

    def one(item: tuple[str, dict]) -> None:
        path, vals = item
        p = Path(path)
        s0 = scan.get(path) or {}
        res = "записано"
        try:
            stat = p.stat()
        except OSError:
            res = "нет файла"
        else:
            before = read_fields(p)
            todo = {k: v for k, v in vals.items() if before.get(k) != v}
            if not todo:
                res = "уже записано"
            elif stat.st_size != s0.get("_size") or int(stat.st_mtime) != s0.get("_mtime"):
                res = "изменён после dry_run — пропущен"
            else:
                write_fields(p, todo)
                after = p.stat()
                line = json.dumps({"ts": int(time.time()), "path": path, "before": {k: before.get(k) for k in todo},
                                   "after": todo, "size": [stat.st_size, after.st_size]}, ensure_ascii=False)
                with lock:
                    log.write(line + "\n")
                    log.flush()
                    # кэш тегов — в актуальное состояние, чтобы следующий dry_run не перечитывал файл
                    s0.update(_size=after.st_size, _mtime=int(after.st_mtime))
                    if "RELEASETYPE" in todo:
                        s0["releasetype"] = todo["RELEASETYPE"]
                    st["размер изменился"] += after.st_size != stat.st_size
        with lock:
            st[res] += 1
            n = sum(st[k] for k in ("записано", "уже записано", "нет файла", "изменён после dry_run — пропущен"))
            if n % 1000 == 0:
                print(f"  {n}/{len(items)} {dict(st)}", flush=True)

    with LOG.open("a", encoding="utf-8") as log, ThreadPoolExecutor(workers) as ex:
        list(ex.map(one, items))
    from dry_run import write_json
    mine = {p for p, _ in items}
    write_json(SCAN, {**read_json(SCAN, {}), **{k: v for k, v in scan.items() if k in mine}})
    return st


def undo() -> collections.Counter:
    st = collections.Counter()
    if not LOG.exists():
        return st
    entries = [json.loads(l) for l in LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    for e in reversed(entries):
        p = Path(e["path"])
        if not p.exists():
            st["нет файла"] += 1
            continue
        write_fields(p, e["before"])
        st["откачено"] += 1
    LOG.rename(LOG.with_name(f"apply_log.undone.{int(time.time())}.jsonl"))
    return st


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--sample", type=int)
    g.add_argument("--barcode", action="append")
    g.add_argument("--all", action="store_true")
    g.add_argument("--undo", action="store_true")
    ap.add_argument("--from", dest="start", type=int, default=0, help="с какого файла плана (для --all)")
    ap.add_argument("--to", dest="end", type=int, default=None, help="до какого файла плана (не включая)")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    if a.undo:
        print(dict(undo()))
        return
    releases = read_json(OUT / "dry_run.json", {}).get("releases") or []
    if a.sample:
        chosen = pick_sample(releases, a.sample)
    elif a.barcode:
        chosen = [r for r in releases if r["barcode"] in a.barcode]
    else:
        chosen = releases
    items = plan(chosen)[a.start:a.end]
    print(f"релизов: {sum(r['status'] in WRITABLE for r in chosen)}, файлов: {len(items)}")
    if a.sample:
        for r in chosen:
            print(f"  {r['barcode']} | {r['record_type']:<11} | {r['status']:<8}{' isrc' if r['via_isrc'] else ''}"
                  f"{' (тип уже есть: ' + str(r['releasetype_tag']) + ')' if r['releasetype_tag'] else ''} | {r['dirs'][0]}")
    print(dict(apply(items, a.workers)))


if __name__ == "__main__":
    main()
