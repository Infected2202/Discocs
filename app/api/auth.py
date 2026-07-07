"""Auth API routes — Phase 1 access gate.

Mounted under /api/v1/auth (the ``api`` prefix is proxied to the backend by the
frontend nginx). Login verifies credentials against Navidrome and issues an
opaque, HttpOnly session cookie; the password is never stored.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import auth
from app.api.deps import api_error, context
from app.config import AuthSettings

auth_logger = logging.getLogger("discocs.auth")

router = APIRouter(prefix="/api/v1/auth")


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)


# Shared per-IP login throttle (thresholds re-synced from settings per request so
# env overrides in tests/deploys take effect without a process restart).
_limiter = auth.LoginRateLimiter(
    max_attempts=AuthSettings().login_max_attempts,
    window_seconds=AuthSettings().login_lockout_seconds,
)


def _login_limiter(settings) -> auth.LoginRateLimiter:
    _limiter._max_attempts = settings.auth.login_max_attempts
    _limiter._window = settings.auth.login_lockout_seconds
    return _limiter


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_https(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto")
    if proto:
        return proto.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


def _set_session_cookie(response: JSONResponse, request: Request, settings, token: str) -> None:
    response.set_cookie(
        key=settings.auth.session_cookie_name,
        value=token,
        max_age=settings.auth.session_ttl_hours * 3600,
        httponly=True,
        secure=_is_https(request),
        samesite="lax",
        path="/",
    )


@router.post("/login")
async def login(request: Request, body: LoginRequest) -> JSONResponse:
    from starlette.concurrency import run_in_threadpool

    store, settings = context()
    ip = _client_ip(request)
    limiter = _login_limiter(settings)

    if limiter.is_locked(ip):
        auth_logger.warning("Login locked out ip=%s user=%s", ip, body.username)
        return api_error(429, "too_many_attempts", "Too many attempts. Try again later.")

    ok = await run_in_threadpool(
        auth.verify_navidrome_credentials, settings, body.username, body.password
    )
    if not ok:
        limiter.record_failure(ip)
        auth_logger.warning("Login failed ip=%s user=%s", ip, body.username)
        return api_error(401, "invalid_credentials", "Invalid username or password.")

    limiter.record_success(ip)
    token = await run_in_threadpool(
        auth.create_session,
        store,
        settings,
        body.username,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
    )
    auth_logger.info("Login ok ip=%s user=%s", ip, body.username)
    response = JSONResponse({"authenticated": True, "username": body.username})
    _set_session_cookie(response, request, settings, token)
    return response


@router.post("/logout")
async def logout(request: Request) -> JSONResponse:
    from starlette.concurrency import run_in_threadpool

    store, settings = context()
    token = request.cookies.get(settings.auth.session_cookie_name)
    await run_in_threadpool(auth.revoke_session, store, token)
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(settings.auth.session_cookie_name, path="/")
    return response


@router.get("/session")
async def session(request: Request) -> dict[str, object]:
    from starlette.concurrency import run_in_threadpool

    store, settings = context()
    if not settings.auth.enabled:
        # Gate off — treat everyone as authenticated so the SPA never blocks.
        return {"authenticated": True, "username": None, "enabled": False}
    token = request.cookies.get(settings.auth.session_cookie_name)
    username = await run_in_threadpool(auth.resolve_session, store, token)
    return {
        "authenticated": username is not None,
        "username": username,
        "enabled": True,
    }
