"""HTTP request logging middleware.

Extracted from app/main.py — Stage 6f.
"""
from __future__ import annotations

import logging
from time import perf_counter

from starlette.requests import Request

logger = logging.getLogger(__name__)


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if request.url.path.startswith("/api/v1/auth/"):
        response.headers.setdefault("Cache-Control", "no-store")
    if request.url.path.startswith("/api/v1/public/shares/"):
        response.headers.setdefault("Cache-Control", "private, no-store")
        response.headers["Referrer-Policy"] = "no-referrer"
        is_unfurl_resource = request.url.path.endswith("/preview") or (
            request.url.path.endswith("/cover")
            and request.query_params.get("preview") == "1"
        )
        if not is_unfurl_resource:
            response.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")
    return response


def should_log_http_request(path: str) -> bool:
    if path in {"/api/v1/stats", "/api/v1/jobs"}:
        return True
    if path.startswith(("/api/v1/metrics", "/api/v1/navidrome", "/api/v1/instant-mix", "/api/v1/text-search")):
        return True
    return path.startswith("/api/v1/tracks/") and path.endswith(
        ("/cover", "/similar", "/navidrome-star")
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
