"""Sweeping work files the bot could not delete itself.

Every delivery removes its own temporary files, but a kill between transcode
and upload leaves them behind — and with links in the picture those files are
now tens of megabytes each, not a cover thumbnail. The sweep runs at startup,
which is exactly when a previous crash has just been recovered from.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def sweep_stale_files(
    directory: Path,
    *,
    max_age_seconds: float,
    now: float | None = None,
) -> list[Path]:
    """Delete files older than the cutoff. Subdirectories are left alone."""
    if not directory.exists():
        return []
    cutoff = (now if now is not None else time.time()) - max_age_seconds
    removed: list[Path] = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
        except OSError:
            logger.debug("Could not sweep %s", path)
            continue
        removed.append(path)
    if removed:
        logger.info("Swept %s stale files from %s", len(removed), directory)
    return removed
