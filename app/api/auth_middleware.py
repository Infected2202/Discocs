"""Access-gate middleware — Phase 1.

Enforced in the backend (not just the SPA) so /admin and every API router are
covered: a login page in the frontend is cosmetic if the API stays open.

A request is authorized if ANY of:
  * the gate is disabled (``DISCOCS_AUTH_ENABLED`` unset/false), or
  * the path is public (health + the auth endpoints themselves), or
  * it carries a valid ``X-Discocs-Service-Token`` (machine principal:
    workers/bot/plugin), or
  * it carries a valid session cookie.

Otherwise it is rejected with 401. See docs/auth.md.
"""
from __future__ import annotations

import os
import re

from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from app import auth
from app.config import Settings
from app.store import Store
from app.user_context import (
    NavidromeCredentials,
    reset_current_navidrome_credentials,
    reset_current_user_id,
    set_current_navidrome_credentials,
    set_current_user_id,
)


def _gate_enabled() -> bool:
    """Cheap env-only check so the disabled default adds no per-request file IO.

    (Settings.from_env reads settings.json from disk; skip it when the gate is
    off, which is the default for every request in the current deployment.)
    """
    return os.getenv("DISCOCS_AUTH_ENABLED", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


# Reachable without a session: liveness probe and the auth handshake endpoints.
PUBLIC_PATHS = frozenset(
    {
        "/health",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/session",
    }
)


UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_PUBLIC_SHARE_PATH = re.compile(
    r"^/api/v1/public/shares/[A-Za-z0-9_-]{40,64}"
    r"(?:/cover|/preview|/items/[0-9]+/audio)?$"
)


def is_public_share_request(request: Request) -> bool:
    configured = os.getenv("DISCOCS_SHARING_ENABLED")
    if configured is not None and configured.strip().lower() not in {
        "1", "true", "yes", "on",
    }:
        return False
    if request.method not in {"GET", "HEAD"}:
        return False
    return _PUBLIC_SHARE_PATH.fullmatch(request.url.path) is not None


def _request_origin(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    scheme = proto.split(",")[0].strip().lower()
    host = request.headers.get("x-forwarded-host", request.headers.get("host", ""))
    host = host.split(",")[0].strip().lower()
    return f"{scheme}://{host}".rstrip("/")


def _origin_allowed(request: Request) -> bool:
    """Reject browser cross-origin mutations; non-browser clients omit Origin."""
    origin = request.headers.get("origin")
    if not origin:
        return True
    allowed = {_request_origin(request)}
    allowed.update(
        value.strip().rstrip("/")
        for value in os.getenv("DISCOCS_CORS_ORIGINS", "").split(",")
        if value.strip()
    )
    normalized = origin.strip().rstrip("/").lower()
    return normalized in {value.lower() for value in allowed}


def _service_token_ok(request: Request, service_token: str) -> bool:
    if not service_token:
        return False
    presented = request.headers.get("x-discocs-service-token", "")
    return bool(presented) and auth.constant_time_eq(presented, service_token)


def _resolve_session(settings: Settings, token: str | None) -> auth.ResolvedSession | None:
    store = Store(settings.db_path)
    store.init()
    return auth.resolve_session(store, token)


async def auth_gate(request: Request, call_next):
    # Cheapest exit first: no Settings/DB work when the gate is off.
    if not _gate_enabled():
        return await call_next(request)

    if request.method == "OPTIONS":
        return await call_next(request)

    if request.method in UNSAFE_METHODS and not _origin_allowed(request):
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "cross_origin_request",
                    "message": "Cross-origin state change rejected.",
                }
            },
        )

    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)

    if is_public_share_request(request):
        request.state.principal = "share"
        request.state.user_id = None
        token_context = set_current_user_id(None)
        nav_context = set_current_navidrome_credentials(None)
        try:
            return await call_next(request)
        finally:
            reset_current_navidrome_credentials(nav_context)
            reset_current_user_id(token_context)

    settings = Settings.from_env()
    # Default: no identity. A service principal has no user_id, so personal
    # endpoints (Phase 2) must refuse it rather than write to owner/NULL.
    request.state.user_id = None
    # Machine principal (workers/bot/plugin) bypasses the session requirement.

    if _service_token_ok(request, settings.auth.service_token):
        request.state.principal = "service"
        token_context = set_current_user_id(None)
        nav_context = set_current_navidrome_credentials(None)
        try:
            return await call_next(request)
        finally:
            reset_current_navidrome_credentials(nav_context)
            reset_current_user_id(token_context)

    token = request.cookies.get(settings.auth.session_cookie_name)
    resolved = await run_in_threadpool(_resolve_session, settings, token)
    if resolved is not None:
        request.state.principal = resolved.username
        request.state.user_id = resolved.user_id
        token_context = set_current_user_id(resolved.user_id)
        credentials = (
            NavidromeCredentials(resolved.username, resolved.navidrome_password)
            if resolved.navidrome_password
            else None
        )
        nav_context = set_current_navidrome_credentials(credentials)
        try:
            return await call_next(request)
        finally:
            reset_current_navidrome_credentials(nav_context)
            reset_current_user_id(token_context)

    return JSONResponse(
        status_code=401,
        content={"error": {"code": "unauthorized", "message": "Authentication required."}},
    )
