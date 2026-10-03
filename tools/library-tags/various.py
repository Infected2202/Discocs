"""Единое имя исполнителя сборников: ALBUMARTIST «Różni wykonawcy», «Различные исполнители», «VA»… → «Various Artists».

    python various.py --dry       # только посчитать
    python various.py             # переписать (вся библиотека, кроме soulseek\\incomplete)
    python various.py --undo      # вернуть как было (по журналу)

Меняется только тег исполнителя альбома (FLAC — ALBUMARTIST, MP3 — TPE2); пути и папки не трогаются —
ID треков в Navidrome остаются прежними. Имена вида «VA (mixed by …)» — это указание диджея, их не трогаем.
Журнал — out/various_log.jsonl.
"""
from __future__ import annotations

import collections
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.id3 import ID3, TPE2, ID3NoHeaderError

from dry_run import OUT

ROOT = Path(r"H:\data\media\music")
SKIP = {ROOT / "soulseek" / "incomplete"}
TARGET = "Various Artists"
VARIANTS = {"różni wykonawcy", "различные исполнители", "разные исполнители", "va", "various", "various artist",
            "various artists"}
LOG = OUT / "various_log.jsonl"


def needs(values: list[str]) -> bool:
    return bool(values) and all(v.strip().lower() in VARIANTS for v in values) and values != [TARGET]


def get(p: Path) -> list[str]:
    if p.suffix.lower() == ".flac":
        return list((FLAC(p).tags or {}).get("albumartist") or [])
    try:
        t = ID3(p)
    except ID3NoHeaderError:
        return []
    fr = t.get("TPE2")
    return [x for v in (fr.text if fr else []) for x in str(v).split("\x00") if x]


def put(p: Path, values: list[str]) -> None:
    if p.suffix.lower() == ".flac":
        f = FLAC(p)
        f.tags["albumartist"] = values
        f.save()
        return
    t = ID3(p)
    t.setall("TPE2", [TPE2(encoding=3, text=values)])
    t.save(p, v2_version=t.version[1] if t.version[1] in (3, 4) else 3)


def files():
    for dirpath, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if Path(dirpath) / d not in SKIP]
        for n in names:
            if n.lower().endswith((".flac", ".mp3")):
                yield Path(dirpath) / n


def run(dry: bool) -> None:
    """Библиотека на SMB-шаре — файлы обрабатываются параллельно (упор в сетевые задержки)."""
    st = collections.Counter()
    lock = threading.Lock()
    t0 = time.time()
    all_files = list(files())
    print(f"файлов: {len(all_files)} ({time.time() - t0:.0f} с на обход)", flush=True)

    def one(p: Path) -> None:
        try:
            vals = get(p)
        except Exception as e:  # битый файл — пропустить
            with lock:
                st[f"не читается: {type(e).__name__}"] += 1
            return
        if not needs(vals):
            return
        if not dry:
            put(p, [TARGET])
        with lock:
            st[" • ".join(vals)] += 1
            if not dry:
                log.write(json.dumps({"ts": int(time.time()), "path": str(p), "before": vals}, ensure_ascii=False) + "\n")
                log.flush()

    with (open(os.devnull, "w") if dry else LOG.open("a", encoding="utf-8")) as log, ThreadPoolExecutor(8) as ex:
        for i, _ in enumerate(ex.map(one, all_files), 1):
            if i % 10000 == 0:
                print(f"  {i}/{len(all_files)}, {time.time() - t0:.0f} с", flush=True)
    print(("будет переписано" if dry else "переписано") + ":", dict(st))


def undo() -> None:
    n = 0
    for line in reversed(LOG.read_text(encoding="utf-8").splitlines()):
        e = json.loads(line)
        put(Path(e["path"]), e["before"])
        n += 1
    LOG.rename(LOG.with_name(f"various_log.undone.{int(time.time())}.jsonl"))
    print("откачено:", n)


if __name__ == "__main__":
    undo() if "--undo" in sys.argv else run("--dry" in sys.argv)
