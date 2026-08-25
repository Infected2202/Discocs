"""Bitrate and splitting decisions for audio downloaded from a link.

Two rules drive everything here:

* never encode above the source bitrate — sources hand out 128-160k opus/aac,
  and re-encoding that to 320k mp3 only grows the file, not the quality;
* a Telegram bot upload is capped at 50 MB, so anything longer is delivered as
  several parts, and beyond a few parts it is not worth sending at all.
"""
from __future__ import annotations

import math

MIN_BITRATE_KBPS = 64
FALLBACK_BITRATE_KBPS = 160
# Splitting re-muxes: headers and tags make parts slightly bigger than a plain
# division of the source suggests.
SPLIT_SAFETY = 0.97


def estimate_bitrate_kbps(size_bytes: int | None, duration_seconds: int | None) -> int | None:
    """Average bitrate of a file, for sources whose streams do not declare one."""
    if not size_bytes or not duration_seconds or duration_seconds <= 0:
        return None
    return max(1, int(size_bytes * 8 / duration_seconds / 1000))


def target_bitrate_kbps(
    source_kbps: int | None,
    *,
    headroom: float = 1.0,
    max_kbps: int = 320,
) -> int:
    """Bitrate to encode at. Never above the source unless headroom says so."""
    base = source_kbps or FALLBACK_BITRATE_KBPS
    scaled = int(round(base * max(1.0, headroom)))
    return max(MIN_BITRATE_KBPS, min(scaled, max_kbps))


def part_count(size_bytes: int, limit_bytes: int) -> int:
    """How many parts a file must be cut into to fit the upload limit."""
    if limit_bytes <= 0:
        raise ValueError("limit_bytes must be positive")
    if size_bytes <= limit_bytes:
        return 1
    return math.ceil(size_bytes / (limit_bytes * SPLIT_SAFETY))


def part_ranges(duration_seconds: float, parts: int) -> list[tuple[float, float]]:
    """Equal (start, length) windows covering the whole file."""
    if parts < 1:
        raise ValueError("parts must be positive")
    if parts == 1:
        return [(0.0, duration_seconds)]
    span = duration_seconds / parts
    return [(index * span, span) for index in range(parts)]


def part_title(title: str, index: int, count: int) -> str:
    """Telegram shows the title in the player; parts must be told apart there."""
    if count <= 1:
        return title
    return f"{title} ({index + 1}/{count})"
