"""Cover art cache helpers.

Extracted from app/main.py.
"""
from __future__ import annotations

import time

from fastapi.responses import Response

from app.state import (
    COVER_CACHE,
    COVER_CACHE_LOCK,
    COVER_CACHE_MAX_ITEMS,
    COVER_CACHE_TTL_SECONDS,
    COVER_ERROR_CACHE,
    COVER_ERROR_CACHE_TTL_SECONDS,
)


def cached_cover_response(cache_key: tuple[str, int]) -> tuple[bytes, str] | None:
    now = time.time()
    with COVER_CACHE_LOCK:
        cached = COVER_CACHE.get(cache_key)
        if cached is None:
            return None
        cached_at, payload, content_type = cached
        if now - cached_at > COVER_CACHE_TTL_SECONDS:
            COVER_CACHE.pop(cache_key, None)
            return None
        return payload, content_type


def cached_cover_error(cache_key: tuple[str, int]) -> str | None:
    now = time.time()
    with COVER_CACHE_LOCK:
        cached = COVER_ERROR_CACHE.get(cache_key)
        if cached is None:
            return None
        cached_at, message = cached
        if now - cached_at > COVER_ERROR_CACHE_TTL_SECONDS:
            COVER_ERROR_CACHE.pop(cache_key, None)
            return None
        return message


def remember_cover(cache_key: tuple[str, int], payload: bytes, content_type: str) -> None:
    with COVER_CACHE_LOCK:
        COVER_CACHE[cache_key] = (time.time(), payload, content_type)
        COVER_ERROR_CACHE.pop(cache_key, None)
        while len(COVER_CACHE) > COVER_CACHE_MAX_ITEMS:
            oldest_key = next(iter(COVER_CACHE))
            COVER_CACHE.pop(oldest_key, None)


def remember_cover_error(cache_key: tuple[str, int], message: str) -> None:
    with COVER_CACHE_LOCK:
        COVER_ERROR_CACHE[cache_key] = (time.time(), message)
        while len(COVER_ERROR_CACHE) > COVER_CACHE_MAX_ITEMS:
            oldest_key = next(iter(COVER_ERROR_CACHE))
            COVER_ERROR_CACHE.pop(oldest_key, None)


def cover_response(payload: bytes, content_type: str) -> Response:
    return Response(
        content=payload,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=86400"},
    )
