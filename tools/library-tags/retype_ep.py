"""EP, которые Deezer записал в album/single и которым мы так и проставили тип, — в ep.

    python retype_ep.py --dry      # что поменяется
    python retype_ep.py            # записать (журнал — out/apply_log.jsonl, откат — apply.py --undo)

Правило то же, что теперь в dry_run.release_type: «… EP», «… (EP)», «… E.P.» в конце названия.
Тип меняется, только если в файле стоит ровно то, что записали мы (по журналу apply): тип из
MusicBrainz и правки руками не трогаем.
"""
from __future__ import annotations

import collections
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from apply import LOG, read_fields, write_fields
from dry_run import EP_TITLE, OUT, read_json


def plan() -> list[tuple[str, str]]:
    """[(путь, тип, который записали мы)] для релизов-EP с типом album/single."""
    written = {}
    for line in LOG.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if "RELEASETYPE" in (e.get("after") or {}):
            written[e["path"]] = e["after"]["RELEASETYPE"]
    out = []
    for r in read_json(OUT / "dry_run.json", {}).get("releases") or []:
        if r.get("record_type") not in ("album", "single") or not EP_TITLE.search((r.get("title") or "").strip()):
            continue
        paths = [t["path"] for t in r.get("tracks") or []]
        if paths and all(written.get(p) == r["record_type"] for p in paths):
            out += [(p, r["record_type"]) for p in paths]
    return out


def main() -> None:
    items = plan()
    print(f"файлов: {len(items)}")
    if "--dry" in sys.argv:
        return
    st, lock = collections.Counter(), threading.Lock()
    with LOG.open("a", encoding="utf-8") as log:
        def one(item: tuple[str, str]) -> None:
            path, ours = item
            p = Path(path)
            try:
                now = read_fields(p).get("RELEASETYPE")
            except Exception as exc:  # noqa: BLE001 — файл пропал/битый: отчитаться и идти дальше
                with lock:
                    st[f"ошибка: {type(exc).__name__}"] += 1
                return
            if now != ours:
                with lock:
                    st["тип уже другой — пропущен"] += 1
                return
            write_fields(p, {"RELEASETYPE": "ep"})
            line = json.dumps({"ts": int(time.time()), "path": path, "before": {"RELEASETYPE": now},
                               "after": {"RELEASETYPE": "ep"}}, ensure_ascii=False)
            with lock:  # одна строка журнала целиком, без перемешивания потоков
                log.write(line + "\n")
                log.flush()
                st["записано"] += 1

        with ThreadPoolExecutor(8) as ex:
            list(ex.map(one, items))
    print(dict(st))


if __name__ == "__main__":
    main()
