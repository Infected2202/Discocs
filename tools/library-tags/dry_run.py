"""Проверочный прогон: сопоставить релизы из deemix с альбомами Deezer по штрихкоду. Файлы не меняет.

    python dry_run.py [папка]          # по умолчанию H:\\data\\media\\music\\Deezer

1. Читает теги всех аудиофайлов (кэш — cache/scan.json, перечитываются только изменённые файлы).
2. Группирует файлы по BARCODE: одна группа — один релиз (синглы deemix лежат россыпью в папке артиста,
   поэтому не по папке).
3. По каждому штрихкоду — `api.deezer.com/album/upc:<barcode>` (с вариантами без ведущих нулей); если
   штрихкода у Deezer больше нет — альбом по ISRC (переиздание, помечается отдельно). Ответ целиком —
   в cache/deezer_albums.json (там же rank/fans — второй раз за ними ходить не нужно).
4. Сверка: каждый файл группы должен найтись в треклисте альбома — название + длительность ±3 с, иначе
   только название.
5. Отчёт — out/dry_run.json (по релизам) и out/dry_run.txt (сводка и проблемы).
"""
from __future__ import annotations

import collections
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

import mutagen
import requests

ROOT = Path(__file__).resolve().parent
SCAN = ROOT / "cache" / "scan.json"
ALBUMS = ROOT / "cache" / "deezer_albums.json"
OUT = ROOT / "out"
AUDIO = (".flac", ".mp3")
DUR_TOL = 3
TYPES = {"album": "album", "ep": "ep", "single": "single", "compile": "compilation"}
EP_TITLE = re.compile(r"(?:^|[\s(\[\-–])E\.?P\.?[)\]]?$", re.I)  # «ADHD EP», «Rise (EP)», «X - E.P.»


def release_type(a: dict) -> str | None:
    """Тип релиза Deezer; EP, которые Deezer записал в album/single («Bangarang EP» — album), — по названию."""
    t = TYPES.get(a.get("record_type"), a.get("record_type"))
    if t in ("album", "single") and EP_TITLE.search((a.get("title") or "").strip()):
        return "ep"
    return t


def read_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


# ---------- теги файлов ----------

def first(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, list):
        v = v[0] if v else None
    if hasattr(v, "text"):  # ID3-кадр
        v = v.text[0] if v.text else None
    return str(v).strip() if v not in (None, "") else None


def file_tags(p: Path) -> dict:
    f = mutagen.File(p)
    if f is None:
        return {"error": "не аудио"}
    t = f.tags or {}
    out = {"length": round(f.info.length, 1) if getattr(f, "info", None) else None}
    get = lambda k: first(t.get(k))
    if p.suffix.lower() == ".flac":
        out.update(title=get("title"), album=get("album"), artist=get("artist"), barcode=get("barcode"),
                   isrc=get("isrc"), label=get("publisher") or get("label") or get("organization"),
                   track=get("tracknumber"), disc=get("discnumber"), releasetype=get("releasetype"),
                   deezer_album_id=get("deezer_album_id"))
    else:
        txxx = {k[5:].lower(): first(v) for k, v in t.items() if k.startswith("TXXX:")}
        out.update(title=get("TIT2"), album=get("TALB"), artist=get("TPE1"), barcode=txxx.get("barcode"),
                   isrc=get("TSRC"), label=get("TPUB"), track=get("TRCK"), disc=get("TPOS"),
                   releasetype=txxx.get("releasetype") or txxx.get("musicbrainz album type"),
                   deezer_album_id=txxx.get("deezer_album_id"))
    return out


def scan(root: Path) -> dict[str, dict]:
    cache = read_json(SCAN, {})
    seen, fresh = {}, 0
    t0 = time.time()
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.lower().endswith(AUDIO):
                continue
            p = Path(dirpath) / name
            st = p.stat()
            key = str(p)
            c = cache.get(key)
            if not c or c.get("_size") != st.st_size or c.get("_mtime") != int(st.st_mtime):
                try:
                    c = file_tags(p)
                except Exception as e:  # битый файл — в отчёт, не падаем
                    c = {"error": f"{type(e).__name__}: {e}"}
                c["_size"], c["_mtime"] = st.st_size, int(st.st_mtime)
                fresh += 1
                if fresh % 500 == 0:
                    print(f"  теги: прочитано {fresh} ({len(seen)} всего, {time.time() - t0:.0f} с)", flush=True)
                    write_json(SCAN, {**cache, **seen})
            seen[key] = c
    write_json(SCAN, seen)
    print(f"файлов: {len(seen)}, перечитано: {fresh}, {time.time() - t0:.0f} с")
    return seen


# ---------- Deezer ----------

_last = [0.0]


def deezer(path: str) -> dict:
    for _ in range(5):
        wait = 0.12 - (time.time() - _last[0])  # квота 50 запросов / 5 с
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
        return j
    return {"error": {"type": "retry", "message": "не ответил за 5 попыток"}}


def upc_variants(upc: str) -> list[str]:
    """deemix пишет штрихкод с ведущими нулями (0617465007552), у Deezer он может быть без них."""
    core = upc.lstrip("0")
    return list(dict.fromkeys([upc, core, core.zfill(12), core.zfill(13)]))


def full_tracklist(a: dict) -> None:
    """album/… отдаёт не больше 25 треков — длинный треклист догружаем."""
    data = (a.get("tracks") or {}).get("data") or []
    if (a.get("nb_tracks") or 0) > len(data):
        page = deezer(f"album/{a['id']}/tracks?limit=1000")
        if page.get("data"):
            a["tracks"]["data"] = page["data"]


def album_by_upc(upc: str, isrc: str | None, cache: dict) -> dict:
    """Альбом по штрихкоду. Если штрихкода у Deezer больше нет (переиздание), ищем по ISRC — такой
    альбом помечен `_via_isrc`: другой штрихкод = другое издание, в отчёте отдельно."""
    a = cache.get(upc)
    if a is None or ("error" in a and not a.get("_v2")):
        a = {"error": {"message": "no data"}}
        for v in upc_variants(upc):
            a = deezer(f"album/upc:{v}")
            if "error" not in a:
                break
        if "error" in a and isrc:
            t = deezer(f"track/isrc:{isrc}")
            if "error" not in t and (t.get("album") or {}).get("id"):
                b = deezer(f"album/{t['album']['id']}")
                if "error" not in b:
                    a = b
                    a["_via_isrc"] = isrc
        a["_fetched"], a["_v2"] = int(time.time()), True
        cache[upc] = a
    if "error" not in a and not a.get("_full"):
        full_tracklist(a)
        a["_full"] = True
    return a


# ---------- сверка ----------

def norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = re.sub(r"[’'`´]", "", s)  # It's == Its
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s*&\s*", " and ", s)
    s = re.sub(r"[(\[]\s*original mix\s*[)\]]", " ", s)  # Deezer то добавляет, то убирает
    return re.sub(r"[^\w]+", " ", s).strip()


def match_files(files: list[tuple[str, dict]], album: dict) -> tuple[list[dict], list[str], dict]:
    """Каждый файл — в треклист альбома (альбом уже точно определён штрихкодом, сверка — по трекам):
    1) название + длительность ±3 с; 2) только название (Deezer перезалил звук другой длины);
    3) только длительность ±1 с, если такой трек один (Deezer переименовал трек после скачивания).
    Файл, чей трек уже занят другим файлом с тем же названием и длительностью, — дубль."""
    tracks = (album.get("tracks") or {}).get("data") or []
    names = [{norm(d.get("title")), norm(d.get("title_short")),
              norm(f"{d.get('title_short', '')} {d.get('title_version', '')}")} for d in tracks]
    used, matched, rest = set(), [], []
    for path, t in files:
        i = next((i for i, d in enumerate(tracks) if i not in used and norm(t.get("title")) in names[i]
                  and t.get("length") is not None and abs(t["length"] - (d.get("duration") or 0)) <= DUR_TOL), None)
        if i is None:
            rest.append((path, t))
            continue
        used.add(i)
        matched.append({"path": path, "deezer_track_id": tracks[i].get("id"), "rank": tracks[i].get("rank")})
    how = collections.Counter()
    rest2 = []
    for path, t in rest:
        i = next((i for i in range(len(tracks)) if i not in used and norm(t.get("title")) in names[i]), None)
        if i is None:
            rest2.append((path, t))
            continue
        used.add(i)
        how["title_only"] += 1
        matched.append({"path": path, "deezer_track_id": tracks[i].get("id"), "rank": tracks[i].get("rank"),
                        "how": "title_only"})
    missed = []
    for path, t in rest2:
        L = t.get("length")
        cands = [i for i in range(len(tracks)) if i not in used and L is not None
                 and abs(L - (tracks[i].get("duration") or 0)) <= 1]
        if len(cands) == 1:
            used.add(cands[0])
            how["duration_only"] += 1
            matched.append({"path": path, "deezer_track_id": tracks[cands[0]].get("id"),
                            "rank": tracks[cands[0]].get("rank"), "how": "duration_only"})
        elif L is not None and any(norm(t.get("title")) in names[i] and abs(L - (tracks[i].get("duration") or 0))
                                   <= DUR_TOL for i in used):
            how["duplicate"] += 1
            missed.append(path)
        else:
            missed.append(path)
    return matched, missed, dict(how)


def is_cd_split(dirs: list[str]) -> bool:
    return len({str(Path(d).parent) for d in dirs}) == 1 and all(re.fullmatch(r"CD\d+", Path(d).name) for d in dirs)


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"H:\data\media\music\Deezer")
    print(f"папка: {root}")
    files = scan(root)

    groups: dict[str, list[tuple[str, dict]]] = collections.defaultdict(list)
    no_barcode, broken = [], []
    for p, t in files.items():
        if t.get("error"):
            broken.append((p, t["error"]))
        elif t.get("barcode"):
            groups[t["barcode"]].append((p, t))
        else:
            no_barcode.append(p)
    print(f"релизов по штрихкоду: {len(groups)}, без штрихкода: {len(no_barcode)} файлов, не читаются: {len(broken)}")

    cache = read_json(ALBUMS, {})
    rows, t0 = [], time.time()
    for n, (upc, fl) in enumerate(sorted(groups.items()), 1):
        a = album_by_upc(upc, next((t.get("isrc") for _, t in fl if t.get("isrc")), None), cache)
        if n % 500 == 0:
            write_json(ALBUMS, cache)
            print(f"  Deezer: {n}/{len(groups)} ({time.time() - t0:.0f} с)", flush=True)
        dirs = sorted({str(Path(p).parent) for p, _ in fl})
        row = {"barcode": upc, "files": len(fl), "dirs": dirs, "album_tag": fl[0][1].get("album"),
               "label_tag": fl[0][1].get("label"),
               "releasetype_tag": sorted({t.get("releasetype") for _, t in fl if t.get("releasetype")}),
               "duplicate_dirs": len(dirs) > 1 and not is_cd_split(dirs)}
        if "error" in a:
            row.update(status="not_found", error=(a["error"] or {}).get("message"))
            rows.append(row)
            continue
        matched, missed, how = match_files(fl, a)
        if a.get("_via_isrc") and norm(a.get("title")) != norm(row["album_tag"]):
            # по ISRC нашёлся другой релиз с этим треком (сборник, сингл) — не наш
            row.update(status="not_found", error="по ISRC — другой релиз", isrc_candidate=a.get("title"))
            rows.append(row)
            continue
        rtype = release_type(a)
        row.update(
            deezer_album_id=a.get("id"), title=a.get("title"), record_type=rtype, label=a.get("label"),
            nb_tracks=a.get("nb_tracks"), fans=a.get("fans"), release_date=a.get("release_date"),
            via_isrc=bool(a.get("_via_isrc")), upc_deezer=a.get("upc"),
            matched=len(matched), missed=missed, how=how, tracks=matched,
            status="no_match" if not matched else "partial_match" if missed
            # ни один файл не сошёлся по названию, файлов мало и название альбома другое — длительность не доказательство
            else "weak" if how.get("duration_only", 0) == len(matched) and len(matched) < 3
            and norm(a.get("title")) != norm(row["album_tag"])
            else "ok_loose" if how else "ok",
            full=len(matched) == a.get("nb_tracks"),
            label_differs=bool(row["label_tag"]) and norm(row["label_tag"]) != norm(a.get("label")),
            type_conflict=bool(row["releasetype_tag"]) and rtype not in {x.split(";")[0].split("\x00")[0].strip().lower()
                                                                          for x in row["releasetype_tag"]},
        )
        rows.append(row)
    write_json(ALBUMS, cache)

    OUT.mkdir(exist_ok=True)
    write_json(OUT / "dry_run.json", {"root": str(root), "releases": rows, "no_barcode": no_barcode,
                                      "broken": broken})
    report(rows, no_barcode, broken, OUT / "dry_run.txt")


def report(rows, no_barcode, broken, path: Path) -> None:
    by = lambda *st: [r for r in rows if r["status"] in st]
    ok, okt = by("ok"), by("ok_loose")
    direct = [r for r in ok + okt if not r.get("via_isrc")]
    lines = [
        f"релизов (штрихкодов): {len(rows)}",
        f"  по штрихкоду, каждый файл найден в треклисте (название + длительность): "
        f"{sum(not r['via_isrc'] for r in ok)}",
        f"  по штрихкоду, все файлы нашлись, часть — только по названию или только по длительности "
        f"(Deezer переименовал/перезалил): {sum(not r['via_isrc'] for r in okt)}",
        f"  штрихкода у Deezer нет, найдено по ISRC (другое издание), все файлы сошлись: "
        f"{sum(r['via_isrc'] for r in ok + okt)}",
        f"  часть файлов не нашлась в треклисте: {len(by('partial_match'))}"
        f" — из них только из-за дублей файлов: "
        f"{sum(len(r['missed']) == r['how'].get('duplicate', 0) for r in by('partial_match'))}",
        f"  ни один файл не нашёлся: {len(by('no_match'))}",
        f"  сошлось только по длительности, файлов меньше 3 — не доказательство: {len(by('weak'))}",
        f"  не найдено на Deezer ни по штрихкоду, ни по ISRC: {len(by('not_found'))}",
        f"файлов без штрихкода: {len(no_barcode)}; не читаются: {len(broken)}",
        "",
        "тип релиза (сошлось по штрихкоду): " + ", ".join(
            f"{k} {v}" for k, v in collections.Counter(r["record_type"] for r in direct).most_common()),
        f"тип уже есть в тегах: {sum(bool(r['releasetype_tag']) for r in rows)}, из них расходится с Deezer: "
        f"{sum(r.get('type_conflict', False) for r in rows)}",
        f"лейбл в тегах отличается от Deezer: {sum(r.get('label_differs', False) for r in rows)}",
        f"один штрихкод в нескольких папках (не CD1/CD2 — похоже на дубли): {sum(r['duplicate_dirs'] for r in rows)}",
        "",
    ]

    def section(title, items, fmt, limit=80):
        lines.append(f"== {title}: {len(items)}")
        lines.extend(fmt(r) for r in items[:limit])
        if len(items) > limit:
            lines.append(f"  … ещё {len(items) - limit} (полный список — dry_run.json)")
        lines.append("")

    d0 = lambda r: r["dirs"][0].replace("H:\\data\\media\\music\\Deezer\\", "")
    section("часть файлов / ни один не нашёлся", by("partial_match", "no_match"),
            lambda r: f"  {d0(r)} | Deezer: {r['title']} ({r['nb_tracks']} тр.){' [по ISRC]' if r['via_isrc'] else ''}"
                      f" | файлов {r['files']}, не нашлось {len(r['missed'])}"
                      f"{' (дубли ' + str(r['how']['duplicate']) + ')' if r['how'].get('duplicate') else ''}: "
                      + "; ".join(Path(p).name for p in r["missed"][:3]))
    section("найдено по ISRC (другое издание)", [r for r in ok + okt if r["via_isrc"]],
            lambda r: f"  {d0(r)} | {r['barcode']} → {r['upc_deezer']} | Deezer: {r['title']} ({r['record_type']}, "
                      f"{r['nb_tracks']} тр.)")
    section("сошлось нестрого", [r for r in okt if not r["via_isrc"]],
            lambda r: f"  {d0(r)} | {r['how']} из {r['files']}")
    section("сошлось только по длительности (мало файлов)", by("weak"),
            lambda r: f"  {d0(r)} | Deezer: {r['title']} | файлы: " + "; ".join(Path(t['path']).name for t in r["tracks"][:2]))
    section("не найдено на Deezer", by("not_found"),
            lambda r: f"  {r['barcode']} | {d0(r)}" + (f" | по ISRC нашёлся другой: {r['isrc_candidate']}"
                                                      if r.get("isrc_candidate") else ""))
    section("тип в тегах расходится с Deezer", [r for r in rows if r.get("type_conflict")],
            lambda r: f"  {d0(r)} | теги: {r['releasetype_tag']} | Deezer: {r['record_type']}")
    section("лейбл отличается", [r for r in rows if r.get("label_differs")],
            lambda r: f"  {d0(r)} | теги: {r['label_tag']} | Deezer: {r['label']}")
    section("один штрихкод в нескольких папках", [r for r in rows if r["duplicate_dirs"]],
            lambda r: "  " + " || ".join(d.replace("H:\\data\\media\\music\\Deezer\\", "") for d in r["dirs"][:3]))
    section("не читаются", [{"p": p, "e": e} for p, e in broken], lambda r: f"  {r['p']} | {r['e']}")
    dirs = sorted({str(Path(p).parent) for p in no_barcode})
    section("папки с файлами без штрихкода", dirs, lambda d: f"  {d}")

    path.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:15]))
    print(f"отчёт: {path}")


if __name__ == "__main__":
    main()
