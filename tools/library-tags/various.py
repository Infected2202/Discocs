"""Единое имя исполнителя сборников: ALBUMARTIST «Różni wykonawcy», «Различные исполнители», «VA»… → «Various Artists».

    python various.py --dry       # только посчитать
    python various.py             # переписать (вся библиотека, кроме soulseek\\incomplete)
    python various.py --undo      # вернуть как было (по журналу)

Меняется только тег исполнителя альбома — везде, где он записан (FLAC/Opus — ALBUMARTIST, MP3 — TPE2,
TXXX:ALBUMARTIST и APEv2 от старых рипов); пути и папки не трогаются —
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

import mutagen
from mutagen.apev2 import APEBadItemError, APENoHeaderError, APEv2
from mutagen.flac import FLAC
from mutagen.id3 import ID3, TPE2, TXXX, ID3NoHeaderError

from dry_run import OUT

ROOT = Path(r"H:\data\media\music")
SKIP = {ROOT / "soulseek" / "incomplete"}
TARGET = "Various Artists"
VARIANTS = {"różni wykonawcy", "различные исполнители", "разные исполнители", "va", "various", "various artist",
            "various artists"}
LOG = OUT / "various_log.jsonl"


ALBUMARTIST_KEYS = {"albumartist", "album artist", "album_artist"}  # так пишут разные тегеры


def needs(values: list[str]) -> bool:
    return bool(values) and all(v.strip().lower() in VARIANTS for v in values) and values != [TARGET]


def _split(values) -> list[str]:
    return [x for v in values for x in str(v).split("\x00") if x]


def get(p: Path) -> dict[str, list[str]]:
    """Все места, где у файла записан исполнитель альбома: {поле: значения}.
    FLAC/Opus/Ogg — Vorbis comment; MP3 — TPE2, TXXX:ALBUMARTIST / «album artist» и APEv2 (старые рипы)."""
    out: dict[str, list[str]] = {}
    ext = p.suffix.lower()
    if ext in (".flac", ".opus", ".ogg"):
        f = FLAC(p) if ext == ".flac" else mutagen.File(p)
        for k, v in (f.tags or {}).items() if ext != ".flac" else (f.tags or []):
            if k.lower() in ALBUMARTIST_KEYS:
                out.setdefault(f"vorbis:{k}", []).extend(v if isinstance(v, list) else [v])
        return out
    try:
        t = ID3(p)
        if "TPE2" in t:
            out["id3:TPE2"] = _split(t["TPE2"].text)
        for fr in t.getall("TXXX"):
            if fr.desc.lower() in ALBUMARTIST_KEYS:
                out[f"id3:TXXX:{fr.desc}"] = _split(fr.text)
    except ID3NoHeaderError:
        pass
    try:
        for k, v in APEv2(p).items():
            if k.lower() in ALBUMARTIST_KEYS:
                out[f"ape:{k}"] = _split(list(v))
    except (APENoHeaderError, APEBadItemError):  # битый APE-блок — остальные теги всё равно правим
        pass
    return out


def put(p: Path, fields: dict[str, list[str]]) -> None:
    ext = p.suffix.lower()
    if ext in (".flac", ".opus", ".ogg"):
        f = FLAC(p) if ext == ".flac" else mutagen.File(p)
        for fid, vals in fields.items():
            f.tags[fid.split(":", 1)[1]] = vals
        f.save()
        return
    id3 = {k: v for k, v in fields.items() if k.startswith("id3:")}
    ape = {k: v for k, v in fields.items() if k.startswith("ape:")}
    if id3:
        t = ID3(p)
        for fid, vals in id3.items():
            if fid == "id3:TPE2":
                t.setall("TPE2", [TPE2(encoding=3, text=vals)])
            else:
                desc = fid.split(":", 2)[2]
                t.delall(f"TXXX:{desc}")
                t.add(TXXX(encoding=3, desc=desc, text=vals))
        t.save(p, v2_version=t.version[1] if t.version[1] in (3, 4) else 3)
    if ape:
        a = APEv2(p)
        for fid, vals in ape.items():
            a[fid.split(":", 1)[1]] = vals
        a.save(p)


def files():
    for dirpath, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if Path(dirpath) / d not in SKIP]
        for n in names:
            if n.lower().endswith((".flac", ".mp3", ".opus", ".ogg")):
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
            fields = get(p)
        except Exception as e:  # битый файл — пропустить
            with lock:
                st[f"не читается: {type(e).__name__}"] += 1
            return
        bad = {fid: vals for fid, vals in fields.items() if needs(vals)}
        if not bad:
            return
        if not dry:
            put(p, {fid: [TARGET] for fid in bad})
        with lock:
            for fid, vals in bad.items():
                st[f"{fid.split(':')[0]} {' • '.join(vals)}"] += 1
            if not dry:
                log.write(json.dumps({"ts": int(time.time()), "path": str(p), "fields": bad}, ensure_ascii=False) + "\n")
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
        p = Path(e["path"])
        # старый формат журнала — только основное поле (FLAC albumartist / MP3 TPE2)
        put(p, e.get("fields") or {("vorbis:albumartist" if p.suffix.lower() == ".flac" else "id3:TPE2"): e["before"]})
        n += 1
    LOG.rename(LOG.with_name(f"various_log.undone.{int(time.time())}.jsonl"))
    print("откачено:", n)


if __name__ == "__main__":
    undo() if "--undo" in sys.argv else run("--dry" in sys.argv)
