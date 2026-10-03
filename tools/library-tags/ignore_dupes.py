"""Скрыть дубли от Navidrome через .ndignore (файлы остаются на диске, удалить — вручную, если нужно).

    python ignore_dupes.py          # проверить и разложить .ndignore
    python ignore_dupes.py --undo   # убрать всё, что положили

Список — ниже, выбран по out/dry_run.json: оставляется копия полнее (с доложенным из Soulseek), с прослушиваниями,
на своём месте. Перед записью проверяется, что каждый трек скрываемой копии есть в оставляемой
(название + длительность ±1,5 с). Что сделано — out/ignored.json.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import mutagen

from dry_run import OUT, norm

DZ = Path(r"H:\data\media\music\Deezer")
R, V = "Różni wykonawcy", "Различные исполнители"
# (скрыть, оставить): целая папка
FOLDERS = [
    *[(f"{R}\\{R} - {n}", f"{V}\\{V} - {n}") for n in (
        "Bonobo_ One Offs... Remixes & B Sides", "Electrosoul System Presents LiquiDNAtion LP Part 1",
        "Electrosoul System Presents LiquiDNAtion LP Part 2", "Neuropunks 3 LP", "Neuropunks LP", "Neuropunks LP 2",
        "Underside 4", "V_A Light Years Vol. 2", "V_A Light Years Vol.3")],
    (f"{R}\\{R} - Seven Years Of Love, Pt. 1", "Various Artists\\Various Artists - Seven Years Of Love, Pt. 1"),
    ("Various Artists - BREAKWAVE, Pt. 2", "Various Artists\\Various Artists - BREAKWAVE, Pt. 2"),
]
# папка -> шаблоны .ndignore (отдельные файлы)
FILES = {
    "Rrose\\Rrose - Primary Evidence": ["*.mp3"],  # те же треки во FLAC рядом
    # Navidrome превращает шаблон в регулярку: скобки становятся группой, ведущий «/» не работает —
    # поэтому без скобок, через «*»
    "Dusty Kid\\Dusty Kid - Moto Perpetuo": [      # старые названия, Deezer переименовал — новые рядом
        "*Extended Mix*.flac", "*Siasia and 2pm*.flac"],
    "Boys Noize": ["Boys Noize - CDXOTA.flac"],     # тот же файл лежит россыпью в корне Deezer
}


def tracks(d: Path) -> list[tuple[str, float]]:
    out = []
    for f in sorted(d.iterdir()):
        if f.suffix.lower() in (".flac", ".mp3"):
            m = mutagen.File(f, easy=True)
            out.append((norm((m.tags or {}).get("title", [f.stem])[0]), m.info.length))
    return out


def covered(hide: Path, keep: Path) -> list[str]:
    k = tracks(keep)
    return [t for t, L in tracks(hide) if not any(t == kt and abs(L - kl) <= 1.5 for kt, kl in k)]


def main() -> None:
    done = []
    if "--undo" in sys.argv:
        for e in json.loads((OUT / "ignored.json").read_text(encoding="utf-8")):
            Path(e["ndignore"]).unlink(missing_ok=True)
            print("убран", e["ndignore"])
        return
    for hide, keep in FOLDERS:
        h, k = DZ / hide, DZ / keep
        missing = covered(h, k)
        if missing:
            print(f"ПРОПУСК {hide}: в оставляемой копии нет {missing}")
            continue
        (h / ".ndignore").write_text("", encoding="utf-8")
        done.append({"ndignore": str(h / ".ndignore"), "keep": str(k), "patterns": []})
        print(f"скрыта папка {hide}  (остаётся {keep})")
    for d, pats in FILES.items():
        p = DZ / d / ".ndignore"
        if p.exists() and p.read_text(encoding="utf-8").strip() == "":
            print(f"ПРОПУСК {p}: там уже скрыта вся папка")
            continue
        p.write_text("\n".join(pats) + "\n", encoding="utf-8")
        done.append({"ndignore": str(p), "keep": None, "patterns": pats})
        print(f"{p}: {pats}")
    (OUT / "ignored.json").write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
