"""Штрихкод релиза из тегов файла (у backend библиотека смонтирована тем же путём, что у Navidrome)."""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_KEYS = ("BARCODE", "UPC")


def read_barcode(path: Path) -> str | None:
    """BARCODE/UPC из FLAC/Ogg (Vorbis comments), MP3 (TXXX) и M4A (freeform); только цифры."""
    from mutagen import File as MutagenFile  # noqa: PLC0415 — тяжёлый импорт только для синхронизации
    from mutagen import MutagenError  # noqa: PLC0415

    try:
        audio = MutagenFile(path)
    except (MutagenError, OSError) as exc:
        logger.debug("Cannot read tags path=%s error=%s", path, exc)
        return None
    tags = getattr(audio, "tags", None)
    if tags is None:
        return None
    for value in _tag_values(tags):
        code = "".join(ch for ch in value if ch.isdigit())
        if len(code) >= 8:
            return code
    return None


def _tag_values(tags) -> list[str]:
    values: list[str] = []
    if hasattr(tags, "getall"):  # ID3
        for frame in tags.getall("TXXX"):
            if str(getattr(frame, "desc", "")).upper() in _KEYS:
                values += [str(text) for text in frame.text]
        return values
    for key in _KEYS:
        for candidate in (key, key.lower(), f"----:com.apple.iTunes:{key}"):
            try:
                found = tags.get(candidate)
            except (KeyError, ValueError):
                found = None
            for item in found or ():
                values.append(item.decode("utf-8", "ignore") if isinstance(item, bytes) else str(item))
    return values
