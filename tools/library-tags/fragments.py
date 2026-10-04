"""Собрать сборники, которые Navidrome раздробил на однотрековые «альбомы».

    python fragments.py --dry      # посчитать
    python fragments.py            # записать (журнал — out/fragments_log.jsonl)
    python fragments.py --undo

Navidrome группирует альбом по названию + исполнителю альбома. У сборников из старых рипов и паков чартов
исполнителя альбома нет — и каждый трек становится отдельным «альбомом» своего исполнителя.
Вход — out/fragments.json (папка + название альбома, разбитые в Navidrome на несколько альбомов):
- 3+ разных исполнителя треков и ни один не встречается в половине треков — сборник: ALBUMARTIST = Various
  Artists, COMPILATION = 1; при 6+ треках ещё RELEASETYPE = compilation (soundtrack — если в названии
  Soundtrack/OST; если типа ещё нет);
- один исполнитель в 3/4 треков и больше — его альбом с гостями: ALBUMARTIST = он; 50–75 % — не трогаем;
- меньше — исполнитель альбома просто записан по-разному: приводится к самому частому варианту.
«[Unknown Album]» и пустое название — не релиз, не трогаем. Пути не меняются.
"""
from __future__ import annotations

import collections
import re
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import mutagen
from mutagen.flac import FLAC
from mutagen.id3 import ID3, TCMP, TXXX

import various
from dry_run import OUT, read_json

LOG = OUT / "fragments_log.jsonl"
AUDIO = (".flac", ".mp3")


def info(p: Path) -> dict:
    f = mutagen.File(p, easy=True)
    t = f.tags or {}
    g = lambda k: (t.get(k) or [None])[0]
    return {"album": g("album"), "artist": g("artist"), "albumartist": g("albumartist")}


def releasetype(p: Path) -> str | None:
    if p.suffix.lower() == ".flac":
        return ((FLAC(p).tags or {}).get("releasetype") or [None])[0]
    t = ID3(p)
    for k in ("TXXX:RELEASETYPE", "TXXX:MusicBrainz Album Type"):
        if k in t:
            return str(t[k].text[0])
    return None


def write(p: Path, albumartist: str, compilation: bool, rtype: str | None) -> dict:
    """Возвращает «что было» для отката."""
    before = {"albumartist_fields": various.get(p)}
    fields = {fid: [albumartist] for fid in before["albumartist_fields"]}
    fields.setdefault("vorbis:albumartist" if p.suffix.lower() == ".flac" else "id3:TPE2", [albumartist])
    various.put(p, fields)
    if p.suffix.lower() == ".flac":
        f = FLAC(p)
        before["compilation"] = (f.tags.get("compilation") or [None])[0]
        before["releasetype"] = (f.tags.get("releasetype") or [None])[0]
        if compilation:
            f.tags["compilation"] = ["1"]
        if rtype and not before["releasetype"]:
            f.tags["releasetype"] = [rtype]
        f.save()
    else:
        t = ID3(p)
        before["compilation"] = str(t["TCMP"].text[0]) if "TCMP" in t else None
        before["releasetype"] = str(t["TXXX:RELEASETYPE"].text[0]) if "TXXX:RELEASETYPE" in t else None
        if compilation:
            t.setall("TCMP", [TCMP(encoding=3, text=["1"])])
        if rtype and not before["releasetype"]:
            t.setall("TXXX:RELEASETYPE", [TXXX(encoding=3, desc="RELEASETYPE", text=[rtype])])
        t.save(p, v2_version=t.version[1] if t.version[1] in (3, 4) else 3)
    return before


def plan() -> list[dict]:
    out = []
    for d, album, _n, _frags in read_json(OUT / "fragments.json", []):
        if not album or album.strip("[]").lower() in ("unknown album", ""):
            continue
        files = [Path(d) / f for f in sorted(os.listdir(d)) if f.lower().endswith(AUDIO)]
        tags = {p: info(p) for p in files}
        mine = [p for p in files if tags[p]["album"] == album]
        if len(mine) < 2:
            continue
        artists = {(tags[p]["artist"] or "").strip().lower() for p in mine}
        lead, share = lead_artist([tags[p]["artist"] for p in mine])
        soundtrack = bool(re.search(r"soundtrack|\bost\b", f"{album} {d}", re.I))
        if len(artists) >= 3 and share < 0.5:
            out.append({"dir": d, "album": album, "files": [str(p) for p in mine], "albumartist": various.TARGET,
                        "compilation": True,
                        "type": "soundtrack" if soundtrack else "compilation" if len(mine) >= 6 else None,
                        "why": f"{len(artists)} исполнителей"})
            continue
        if len(artists) >= 3:  # альбом исполнителя с гостями: он — в 3/4 треков и больше
            if share < 0.75:
                continue  # 50–75 % — непонятно, сборник это или альбом с гостями: не трогаем
            common, why = lead, f"альбом {lead} с гостями ({share:.0%} треков)"
        else:
            common = collections.Counter(tags[p]["albumartist"] or tags[p]["artist"] for p in mine).most_common(1)[0][0]
            why = "разное написание исполнителя альбома"
        if not (common or "").strip():
            continue
        out.append({"dir": d, "album": album, "files": [str(p) for p in mine], "albumartist": common.strip(),
                    "compilation": False, "type": None, "why": why})
    return out


SPLIT = re.compile(r"\s*(?:,|&|\bfeat\.?|\bft\.?|\bfeaturing\b|\bvs\.?|\bx\b|•|/|\band\b)\s*", re.I)


def lead_artist(names: list[str | None]) -> tuple[str, float]:
    """Исполнитель, который есть в наибольшей доле треков (с учётом «A & B», «A feat. B»), и эта доля."""
    seen = collections.Counter()
    spelled = {}
    for n in names:
        parts = {x.strip() for x in SPLIT.split(n or "") if x.strip()}
        for x in parts:
            seen[x.lower()] += 1
            spelled.setdefault(x.lower(), x)
    if not seen:
        return "", 0.0
    top, cnt = seen.most_common(1)[0]
    return spelled[top], cnt / len(names)


def main() -> None:
    if "--undo" in sys.argv:
        for line in reversed(LOG.read_text(encoding="utf-8").splitlines()):
            e = json.loads(line)
            p = Path(e["path"])
            various.put(p, e["before"]["albumartist_fields"] or {})
            if p.suffix.lower() == ".flac":
                f = FLAC(p)
                for k in ("compilation", "releasetype"):
                    if e["before"][k] is None:
                        f.tags.pop(k, None)
                    else:
                        f.tags[k] = [e["before"][k]]
                f.save()
            else:
                t = ID3(p)
                for k, frame in (("compilation", "TCMP"), ("releasetype", "TXXX:RELEASETYPE")):
                    if e["before"][k] is None:
                        t.delall(frame)
                t.save(p, v2_version=t.version[1] if t.version[1] in (3, 4) else 3)
        LOG.rename(LOG.with_name(f"fragments_log.undone.{int(time.time())}.jsonl"))
        return
    groups = plan()
    st = collections.Counter(("сборник" if g["compilation"] else "исполнитель альбома") for g in groups)
    print(f"папок: {len(groups)} ({dict(st)}), файлов: {sum(len(g['files']) for g in groups)}, "
          f"тип compilation: {sum(len(g['files']) for g in groups if g['type'])} файлов")
    for g in groups:
        print(f"  {'VA ' if g['compilation'] else 'AA '} {len(g['files']):3} | {g['album'][:60]} → {g['albumartist']}"
              f"{' + ' + g['type'] if g['type'] else ''} | {g['why']}")
    if "--dry" in sys.argv:
        return
    lock = threading.Lock()
    done = {json.loads(l)["path"] for l in LOG.read_text(encoding="utf-8").splitlines()} if LOG.exists() else set()
    st, failed = collections.Counter(), []
    with LOG.open("a", encoding="utf-8") as log:
        def one(item):
            g, p = item
            if p in done:  # уже записан прошлым запуском — повторно не трогаем, иначе откат вернёт новое
                with lock:
                    st["уже записано"] += 1
                return
            try:
                before = write(Path(p), g["albumartist"], g["compilation"], g["type"])
            except Exception as e:  # например, атрибут «только чтение»
                with lock:
                    st[f"ошибка: {type(e).__name__}"] += 1
                    failed.append(p)
                return
            with lock:
                st["записано"] += 1
                log.write(json.dumps({"ts": int(time.time()), "path": p, "before": before}, ensure_ascii=False) + "\n")
                log.flush()
        with ThreadPoolExecutor(8) as ex:
            list(ex.map(one, [(g, p) for g in groups for p in g["files"]]))
    print(dict(st))
    if failed:
        (OUT / "fragments_failed.txt").write_text("\n".join(failed), encoding="utf-8")
        print("не записано (список — out/fragments_failed.txt), папки:",
              sorted({str(Path(p).parent) for p in failed}))


if __name__ == "__main__":
    main()
