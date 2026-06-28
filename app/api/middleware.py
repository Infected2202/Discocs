"""HTTP request logging middleware.

Extracted from app/main.py — Stage 6f.
"""
from __future__ import annotations

import logging
from time import perf_counter

from starlette.requests import Request

logger = logging.getLogger(__name__)


def should_log_http_request(path: str) -> bool:
    if path in {"/stats", "/jobs"}:
        return True
    if path.startswith(("/metrics", "/navidrome", "/instant-mix", "/text-search")):
        return True
    return path.startswith("/tracks/") and (
        path.endswith("/cover")
        or path.endswith("/similar")
        or path.endswith("/navidrome-star")
    )


async def log_http_request(request: Request, call_next):
    path = request.url.path
    should_log = should_log_http_request(path)
    started = perf_counter()
    if should_log:
        logger.info(
            "HTTP request started method=%s path=%s query=%s",
            request.method,
            path,
            request.url.query,
        )
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "HTTP request failed method=%s path=%s query=%s seconds=%.3f",
            request.method,
            path,
            request.url.query,
            perf_counter() - started,
        )
        raise
    seconds = perf_counter() - started
    if should_log or seconds >= 1.0:
        logger.info(
            "HTTP request completed method=%s path=%s status=%s seconds=%.3f",
            request.method,
            path,
            response.status_code,
            seconds,
        )
    return response
