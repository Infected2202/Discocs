"""Теги одного только что скачанного релиза deemix — вызывается из music-fill, когда задание в deemix завершилось.

    python autotag.py album 123456 "H:\\data\\media\\music\\Deezer\\Artist\\Artist - Album"   # вручную

То же, что массовый прогон (dry_run.py + apply.py + various.py), но на один релиз:
1. альбом Deezer по ID (задание deemix — album_<id> или track_<id>, для трека берётся его альбом);
2. файлы — в папке релиза (extrasPath задания) и вложенных CD1/CD2, только с BARCODE этого альбома
   (синглы deemix лежат россыпью в папке артиста);
3. сверка с треклистом (match_files), и только если сошлись все файлы — RELEASETYPE (если ещё нет),
   DEEZER_ALBUM_ID / DEEZER_TRACK_ID (журнал — out/apply_log.jsonl) и «Różni wykonawcy»… → «Various Artists».
"""
from __future__ import annotations

import json
import sys
import time
import threading
from pathlib import Path

import apply as ap
import various
from dry_run import deezer, release_type, file_tags, full_tracklist, match_files, read_json, write_json

AUDIO = (".flac", ".mp3")
_lock = threading.Lock()  # вызывается из потока music-fill; журнал и кэш тегов — общие


def same_upc(a: str | None, b: str | None) -> bool:
    return bool(a and b) and a.lstrip("0") == b.lstrip("0")


def tag_release(kind: str, deezer_id: int | str, folder: str | None) -> dict:
    """Результат: {status: ok | partial | no_files | not_found, type, files, written, absent, ...} — для журнала
    music-fill; absent — треки релиза на Deezer, для которых в папке нет файла."""
    if kind == "track":
        t = deezer(f"track/{deezer_id}")
        if "error" in t:
            return {"status": "not_found", "error": (t["error"] or {}).get("message")}
        deezer_id = t["album"]["id"]
    a = deezer(f"album/{deezer_id}")
    if "error" in a:
        return {"status": "not_found", "error": (a["error"] or {}).get("message")}
    full_tracklist(a)
    rtype = release_type(a)
    out = {"album": a.get("title"), "deezer_album_id": a["id"], "type": rtype}
    d = Path(folder) if folder else None
    if not d or not d.is_dir():
        return {**out, "status": "no_files", "error": f"нет папки {folder}"}
    files = []
    for p in sorted(d.rglob("*")):
        if p.suffix.lower() in AUDIO and not p.name.startswith(".part-"):
            t = file_tags(p)
            if same_upc(t.get("barcode"), a.get("upc")):
                files.append((str(p), t))
    if not files:
        return {**out, "status": "no_files", "error": "нет файлов с этим штрихкодом"}
    matched, missed, how = match_files(files, a)
    have = {m["deezer_track_id"] for m in matched}
    # треки релиза без файла — music-fill отправит их в Soulseek, если deemix их так и не скачал
    absent = [{"id": t["id"], "title": t.get("title"), "artist": (t.get("artist") or {}).get("name")}
              for t in (a.get("tracks") or {}).get("data") or [] if t.get("id") not in have]
    out.update(files=len(files), matched=len(matched), absent=absent)
    if missed:
        return {**out, "status": "partial", "missed": [Path(p).name for p in missed][:5]}
    typed = any(t.get("releasetype") for _, t in files)
    items = []
    for m in matched:
        vals = {"DEEZER_ALBUM_ID": str(a["id"]), "DEEZER_TRACK_ID": str(m["deezer_track_id"])}
        if not typed and rtype:
            vals["RELEASETYPE"] = rtype
        items.append((m["path"], vals))
    with _lock:
        scan = read_json(ap.SCAN, {})
        for p, t in files:  # кэш тегов — текущее состояние файлов, иначе apply сочтёт их изменёнными
            st = Path(p).stat()
            scan[p] = {**t, "_size": st.st_size, "_mtime": int(st.st_mtime)}
        write_json(ap.SCAN, scan)
        st = ap.apply(items, workers=4)
        va = 0
        for p, _ in files:
            bad = {fid: vals for fid, vals in various.get(Path(p)).items() if various.needs(vals)}
            if bad:
                various.put(Path(p), {fid: [various.TARGET] for fid in bad})
                with various.LOG.open("a", encoding="utf-8") as log:  # для various.py --undo
                    log.write(json.dumps({"ts": int(time.time()), "path": p, "fields": bad}, ensure_ascii=False) + "\n")
                va += 1
    return {**out, "status": "ok", "written": st.get("записано", 0), "type_already": typed, "various_artists": va}


if __name__ == "__main__":
    print(tag_release(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None))
