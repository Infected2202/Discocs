"""Disk cache for audio downloaded from links.

Downloads are kept instead of deleted: the same link is usually asked for twice
(send me the file, then build radio from it), and a second download costs both
traffic and another round with the source. The directory is bounded, so the
cache is trimmed oldest-first rather than growing forever.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

SAFE_NAME = re.compile(r"[^A-Za-z0-9_-]+")
# yt-dlp drops the thumbnail next to the audio; only audio is a cache hit.
AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".mp4", ".aac", ".opus", ".ogg", ".webm", ".flac", ".wav", ".oga",
}


def safe_stem(key: str) -> str:
    """Filesystem-safe stem. Media keys come from remote metadata."""
    cleaned = SAFE_NAME.sub("_", key).strip("_")
    return cleaned or "media"


class MediaCache:
    def __init__(self, root: Path, max_bytes: int) -> None:
        self.root = root
        self.max_bytes = max(0, max_bytes)

    def ensure_root(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def find(self, key: str) -> Path | None:
        """Newest cached file for the key, refreshed so trimming keeps it."""
        if not self.root.exists():
            return None
        matches = sorted(
            (
                path
                for path in self.root.glob(f"{safe_stem(key)}.*")
                if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not matches:
            return None
        newest = matches[0]
        self.touch(newest)
        return newest

    @staticmethod
    def touch(path: Path) -> None:
        try:
            path.touch()
        except OSError:
            logger.debug("Could not touch cached file %s", path)

    def total_bytes(self) -> int:
        if not self.root.exists():
            return 0
        return sum(path.stat().st_size for path in self.root.iterdir() if path.is_file())

    def trim(self) -> list[Path]:
        """Delete least-recently-used files until the cache fits its limit."""
        if not self.root.exists():
            return []
        files = [path for path in self.root.iterdir() if path.is_file()]
        total = sum(path.stat().st_size for path in files)
        if total <= self.max_bytes:
            return []

        removed: list[Path] = []
        for path in sorted(files, key=lambda item: item.stat().st_mtime):
            if total <= self.max_bytes:
                break
            size = path.stat().st_size
            try:
                path.unlink()
            except OSError:
                logger.warning("Could not evict cached file %s", path)
                continue
            total -= size
            removed.append(path)
        if removed:
            logger.info(
                "Trimmed media cache: removed=%s remaining_bytes=%s limit=%s",
                len(removed),
                total,
                self.max_bytes,
            )
        return removed
