from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AudioMetadata:
    artist: str | None
    title: str | None
    album: str | None
    album_artist: str | None
    genre: str | None
    year: int | None
    duration: float | None
    track_number: int | None = None
    disc_number: int | None = None
    total_tracks: int | None = None


def _first_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list | tuple):
        if not value:
            return None
        value = value[0]
    text = str(value).strip()
    return text or None


def _year(value: Any) -> int | None:
    text = _first_text(value)
    if not text:
        return None
    for index in range(max(len(text) - 3, 0)):
        chunk = text[index : index + 4]
        if chunk.isdigit():
            year = int(chunk)
            if 1000 <= year <= 9999:
                return year
    return None


def _number(value: Any) -> int | None:
    text = _first_text(value)
    if not text:
        return None
    first = text.split("/", 1)[0].strip()
    return int(first) if first.isdigit() else None


def read_audio_metadata(path: Path) -> AudioMetadata:
    try:
        from mutagen import File
    except ImportError as exc:
        raise RuntimeError("mutagen is required to read audio metadata") from exc

    parsed = File(path, easy=True)
    if parsed is None:
        return AudioMetadata(
            artist=None,
            title=path.stem,
            album=None,
            album_artist=None,
            genre=None,
            year=None,
            duration=None,
        )

    tags = parsed.tags or {}
    info = getattr(parsed, "info", None)
    duration = getattr(info, "length", None)

    return AudioMetadata(
        artist=_first_text(tags.get("artist")),
        title=_first_text(tags.get("title")) or path.stem,
        album=_first_text(tags.get("album")),
        album_artist=_first_text(
            tags.get("albumartist")
            or tags.get("album artist")
            or tags.get("album_artist")
            or tags.get("albumartistsort")
        ),
        genre=_first_text(tags.get("genre")),
        year=_year(tags.get("date") or tags.get("year") or tags.get("originaldate")),
        duration=float(duration) if duration is not None else None,
        track_number=_number(tags.get("tracknumber") or tags.get("track")),
        disc_number=_number(tags.get("discnumber") or tags.get("disc")),
        total_tracks=_number(tags.get("totaltracks") or tags.get("tracktotal")),
    )
