from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import logging
from pathlib import Path

from app.metadata import AudioMetadata, read_audio_metadata


logger = logging.getLogger(__name__)
AUDIO_EXTENSIONS = {
    ".aif",
    ".aiff",
    ".ape",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
    ".wma",
}


@dataclass(frozen=True)
class ScannedTrack:
    path: Path
    artist: str | None
    title: str | None
    album: str | None
    duration: float | None
    file_size: int
    mtime: int
    genre: str | None = None
    year: int | None = None
    album_artist: str | None = None
    track_number: int | None = None
    disc_number: int | None = None
    total_tracks: int | None = None


def iter_audio_files(root: Path) -> Iterator[Path]:
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
            yield path


def scan_music_folder(root: Path) -> Iterator[ScannedTrack]:
    logger.info("Scanning music folder root=%s", root)
    for path in iter_audio_files(root):
        stat = path.stat()
        metadata = _safe_metadata(path)
        yield ScannedTrack(
            path=path.resolve(),
            artist=metadata.artist,
            title=metadata.title,
            album=metadata.album,
            album_artist=metadata.album_artist,
            genre=metadata.genre,
            year=metadata.year,
            duration=metadata.duration,
            track_number=metadata.track_number,
            disc_number=metadata.disc_number,
            total_tracks=metadata.total_tracks,
            file_size=stat.st_size,
            mtime=int(stat.st_mtime),
        )


def _safe_metadata(path: Path) -> AudioMetadata:
    try:
        return read_audio_metadata(path)
    except Exception:
        logger.warning("Failed to read audio metadata path=%s", path, exc_info=True)
        return AudioMetadata(
            artist=None,
            title=path.stem,
            album=None,
            album_artist=None,
            genre=None,
            year=None,
            duration=None,
        )
