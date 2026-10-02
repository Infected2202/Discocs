"""Раскладка скачанного из Soulseek в библиотеку — так же, как это делает deemix.

Папки (настройки deemix): Артист\\Артист - Альбом\\01 - Трек.flac; сингл из одного трека —
файлом Артист\\Артист - Трек.flac прямо в папке артиста (createSingleFolder: false);
многодисковые — в подпапках CD1/CD2. Недопустимые в именах символы — '_'.
Теги — набор deemix: title, artist(s), album, albumartist, tracknumber, discnumber, date, genre,
isrc, length, barcode, publisher (+ catalognumber, bpm, если известны), обложка — только внутрь файла.
Свои поля (t["custom"]) — во FLAC как есть, в MP3 фреймами TXXX с тем же именем; COMMENT не трогаем:
SOURCE, SOURCE_DATE, SOURCE_FORMAT, SOULSEEK_USER, SOULSEEK_FILE, MATCH, MATCH_DURATION,
BEATPORT_RELEASE_ID / BEATPORT_TRACK_ID или DEEZER_ALBUM_ID / DEEZER_TRACK_ID.
Lossless не во FLAC (WAV/AIFF/ALAC) перегоняется во FLAC через ffmpeg — чтобы теги были как у всех."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import mutagen
import requests
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TALB, TBPM, TCON, TDRC, TIT2, TLEN, TPE1, TPE2, TPOS, TPUB, TRCK, TSRC, TXXX

ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
TO_FLAC = {"wav", "aif", "aiff", "m4a", "alac", "ape", "wv"}


def safe(name: str) -> str:
    """Имя файла/папки как у deemix: недопустимое — '_', без точек и пробелов в конце (Windows)."""
    return ILLEGAL.sub("_", name or "").strip().rstrip(". ") or "_"


def album_dir(root: Path, album_artist: str, album: str) -> Path:
    return root / safe(album_artist) / safe(f"{album_artist} - {album}")


def find_album_dir(root: Path, album: str, artists: list[str]) -> Path | None:
    """Папка альбома, которую уже завёл deemix (докладываем недостающие треки). Альбомного артиста
    у Deezer бывает видно по-разному ('Various Artists' / 'Различные исполнители') — пробуем все."""
    tail = " - " + safe(album)
    for a in artists:
        d = root / safe(a)
        if d.is_dir():
            for x in os.scandir(d):
                if x.is_dir() and x.name.endswith(tail):
                    return Path(x.path)
    for a in os.scandir(root):  # дорогой путь: обход всех артистов
        if a.is_dir():
            p = Path(a.path) / safe(a.name + tail)
            if p.is_dir():
                return p
    return None


def to_flac(src: Path) -> Path:
    dst = src.with_suffix(".flac")
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src), "-map", "0:a",
                    "-c:a", "flac", "-compression_level", "8", str(dst)], check=True, timeout=600)
    src.unlink()
    return dst


def fetch_cover(url: str | None) -> bytes | None:
    if not url:
        return None
    try:
        r = requests.get(url, timeout=30)
        return r.content if r.ok and r.headers.get("content-type", "").startswith("image") else None
    except requests.RequestException:
        return None


def write_tags(path: Path, t: dict, cover: bytes | None) -> None:
    """t: title, artist, artists[], album, albumartist, tracknumber, discnumber, date, genre[],
    isrc, length (с), barcode, label, catno, bpm."""
    ext = path.suffix.lower()
    if ext == ".flac":
        f = FLAC(path)
        f.delete()
        f.clear_pictures()
        tags = {"title": t.get("title"), "artist": t.get("artist"), "album": t.get("album"),
                "albumartist": t.get("albumartist"), "tracknumber": t.get("tracknumber"),
                "discnumber": t.get("discnumber") or 1, "date": t.get("date"), "isrc": t.get("isrc"),
                "length": int(t["length"] * 1000) if t.get("length") else None, "barcode": t.get("barcode"),
                "publisher": t.get("label"), "catalognumber": t.get("catno"), "bpm": t.get("bpm"),
                }
        tags |= t.get("custom") or {}  # свои поля (откуда файл, как сопоставлен, ID каталога)
        for k, v in tags.items():
            if v not in (None, ""):
                f[k] = str(v)
        if len(t.get("artists") or []) > 1:
            f["artists"] = t["artists"]
        if t.get("genre"):
            f["genre"] = t["genre"]
        if cover:
            pic = Picture()
            pic.type, pic.mime, pic.data = 3, "image/jpeg", cover
            f.add_picture(pic)
        f.save()
    elif ext == ".mp3":
        try:
            f = ID3(path)
            f.delete(path)
        except mutagen.MutagenError:
            pass
        f = ID3()
        f.add(TIT2(encoding=3, text=t.get("title") or ""))
        f.add(TPE1(encoding=3, text=t.get("artist") or ""))
        f.add(TALB(encoding=3, text=t.get("album") or ""))
        f.add(TPE2(encoding=3, text=t.get("albumartist") or ""))
        if t.get("tracknumber"):
            f.add(TRCK(encoding=3, text=str(t["tracknumber"])))
        f.add(TPOS(encoding=3, text=str(t.get("discnumber") or 1)))
        for frame, key in ((TDRC, "date"), (TSRC, "isrc"), (TPUB, "label"), (TBPM, "bpm")):
            if t.get(key):
                f.add(frame(encoding=3, text=str(t[key])))
        if t.get("length"):
            f.add(TLEN(encoding=3, text=str(int(t["length"] * 1000))))
        if t.get("genre"):
            f.add(TCON(encoding=3, text=t["genre"]))
        for desc, key in (("BARCODE", "barcode"), ("CATALOGNUMBER", "catno")):
            if t.get(key):
                f.add(TXXX(encoding=3, desc=desc, text=str(t[key])))
        for desc, v in (t.get("custom") or {}).items():
            if v not in (None, ""):
                f.add(TXXX(encoding=3, desc=desc.upper(), text=str(v)))
        if cover:
            f.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="cover", data=cover))
        f.save(path, v2_version=3, v1=2)
    else:
        raise RuntimeError(f"не умею тегировать {ext}")


ALBUM_KEYS = {"album": "album", "albumartist": "albumartist", "genre": "genre", "date": "date",
              "publisher": "label", "barcode": "barcode"}


def sibling_album_tags(dest_dir: Path) -> dict:
    """Альбомные теги соседей по папке (её завёл deemix): докладываемый трек должен совпадать с ними —
    альбомный артист 'Различные исполнители', жанры по-русски и т.п., иначе плеер разобьёт альбом."""
    if not dest_dir.is_dir():
        return {}
    for f in sorted(dest_dir.iterdir()):
        if f.suffix.lower() in (".flac", ".mp3") and not f.name.startswith(".part-"):
            try:
                m = mutagen.File(f, easy=True)
            except mutagen.MutagenError:
                continue
            if not m or not m.tags:
                continue
            out = {}
            for k, ours in ALBUM_KEYS.items():
                v = m.tags.get(k)
                if v:
                    out[ours] = list(v) if ours == "genre" else v[0]
            return out
    return {}


def place(src: Path, dest_dir: Path, name: str, tags: dict, cover: bytes | None, single_file: bool = False) -> Path:
    """Перенести файл из папки slskd в библиотеку: lossless → FLAC, имя, теги. Существующий файл
    не перезаписываем — оставляем как есть (вдруг его уже положил deemix)."""
    ext = src.suffix.lower().lstrip(".")
    dest_dir.mkdir(parents=True, exist_ok=True)
    work = dest_dir / f".part-{safe(name)}.{ext}"
    shutil.copy2(src, work)
    if ext in TO_FLAC:
        work = to_flac(work)
    dst = dest_dir / f"{safe(name)}{work.suffix.lower()}"
    if dst.exists():
        work.unlink()
        return dst
    write_tags(work, tags | sibling_album_tags(dest_dir) if not single_file else tags, cover)
    work.replace(dst)
    src.unlink()
    return dst
