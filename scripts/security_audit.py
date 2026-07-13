#!/usr/bin/env python3
"""Safe, repeatable black-box security checks for a discocs deployment."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


@dataclass(frozen=True)
class Result:
    status: int
    headers: dict[str, str]
    body: bytes


def request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: object | None = None,
    timeout: float = 20.0,
) -> Result:
    body = None
    request_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    req = Request(
        base_url.rstrip("/") + path,
        data=body,
        headers=request_headers,
        method=method,
    )
    try:
        with urlopen(req, timeout=timeout) as response:
            return Result(
                response.status,
                {key.lower(): value for key, value in response.headers.items()},
                response.read(),
            )
    except HTTPError as exc:
        return Result(
            exc.code,
            {key.lower(): value for key, value in exc.headers.items()},
            exc.read(),
        )


class Audit:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.passes: list[str] = []

    def expect(self, name: str, condition: bool, detail: str) -> None:
        if condition:
            self.passes.append(name)
        else:
            self.failures.append(f"{name}: {detail}")

    def status(self, name: str, result: Result, expected: int) -> None:
        self.expect(name, result.status == expected, f"expected {expected}, got {result.status}")


def audit_backend(base_url: str, *, brute_force: bool, attempts: int) -> Audit:
    audit = Audit()

    health = request(base_url, "/health")
    audit.status("health is public", health, 200)

    session = request(base_url, "/api/v1/auth/session")
    audit.status("auth session handshake is public", session, 200)
    try:
        session_payload: Any = json.loads(session.body)
    except json.JSONDecodeError:
        session_payload = {}
    audit.expect(
        "auth gate is enabled",
        session_payload.get("enabled") is True,
        f"session payload was {session_payload!r}",
    )

    protected = (
        "/api/v1/dashboard",
        "/admin",
        "/debug/ui",
        "/api/v1/workers",
        "/api/v1/settings/navidrome",
        "/api/v1/jobs",
        "/api/map/projections",
        "/openapi.json",
        "/docs",
    )
    for path in protected:
        result = request(base_url, path)
        audit.status(f"anonymous request blocked: {path}", result, 401)

    invalid_cookie = request(
        base_url,
        "/api/v1/dashboard",
        headers={"Cookie": "discocs_session=definitely-invalid"},
    )
    audit.status("forged session cookie rejected", invalid_cookie, 401)

    fake_service = request(
        base_url,
        "/api/v1/workers",
        headers={"X-Discocs-Service-Token": "definitely-invalid"},
    )
    audit.status("forged service token rejected", fake_service, 401)

    mutation = request(
        base_url,
        "/api/v1/playlists",
        method="POST",
        payload={"title": "audit-must-not-be-created", "track_ids": []},
    )
    audit.status("anonymous mutation blocked", mutation, 401)

    cross_origin = request(
        base_url,
        "/api/v1/auth/login",
        method="POST",
        headers={"Origin": "https://evil.invalid"},
        payload={"username": "security-audit", "password": "invalid"},
    )
    audit.status("cross-origin login rejected before IdP", cross_origin, 403)

    malformed = request(
        base_url,
        "/api/v1/auth/login",
        method="POST",
        headers={"Content-Type": "application/json"},
        payload=None,
    )
    audit.expect(
        "empty login payload rejected",
        malformed.status in {400, 422},
        f"expected 400/422, got {malformed.status}",
    )

    invalid_payloads = (
        (
            "oversized username rejected",
            {"username": "x" * 257, "password": "invalid"},
        ),
        (
            "control character username rejected",
            {"username": "audit\nforged", "password": "invalid"},
        ),
        (
            "unknown login field rejected",
            {"username": "audit", "password": "invalid", "admin": True},
        ),
    )
    for name, payload in invalid_payloads:
        result = request(
            base_url,
            "/api/v1/auth/login",
            method="POST",
            payload=payload,
        )
        audit.status(name, result, 422)
    required_headers = {
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "referrer-policy": "strict-origin-when-cross-origin",
        "permissions-policy": "camera=(), microphone=(), geolocation=()",
    }
    for name, expected in required_headers.items():
        audit.expect(
            f"security header {name}",
            session.headers.get(name) == expected,
            f"expected {expected!r}, got {session.headers.get(name)!r}",
        )
    audit.expect(
        "auth responses are not cacheable",
        session.headers.get("cache-control") == "no-store",
        f"got {session.headers.get('cache-control')!r}",
    )

    if brute_force:
        fixed_user = f"discocs-audit-fixed-{uuid4().hex}"
        fixed_statuses = []
        for number in range(attempts):
            fixed_statuses.append(
                request(
                    base_url,
                    "/api/v1/auth/login",
                    method="POST",
                    headers={"X-Forwarded-For": "198.51.100.10"},
                    payload={"username": fixed_user, "password": f"invalid-{number}"},
                ).status
            )
        audit.expect(
            "fixed-source brute force is throttled",
            429 in fixed_statuses,
            f"statuses were {fixed_statuses}",
        )

        rotated_user = f"discocs-audit-rotated-{uuid4().hex}"
        rotated_statuses = []
        for number in range(attempts):
            rotated_statuses.append(
                request(
                    base_url,
                    "/api/v1/auth/login",
                    method="POST",
                    headers={"X-Forwarded-For": f"203.0.113.{number + 1}"},
                    payload={"username": rotated_user, "password": f"invalid-{number}"},
                ).status
            )
        audit.expect(
            "rotating-source brute force is throttled per account",
            429 in rotated_statuses,
            f"statuses were {rotated_statuses}",
        )

    return audit


def audit_frontend(base_url: str, audit: Audit) -> None:
    for path in (
        "/admin",
        "/api/map/projections",
        "/api/v1/workers",
        "/api/v1/settings/navidrome",
        "/api/v1/jobs",
    ):
        result = request(base_url, path)
        audit.status(f"public frontend hides operational route: {path}", result, 404)

    root = request(base_url, "/")
    audit.status("public frontend responds", root, 200)
    audit.expect(
        "public frontend has CSP",
        "content-security-policy" in root.headers,
        "Content-Security-Policy header missing",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-url", required=True)
    parser.add_argument("--frontend-url")
    parser.add_argument(
        "--brute-force",
        action="store_true",
        help="send controlled invalid-login bursts using unique fake accounts",
    )
    parser.add_argument("--attempts", type=int, default=6)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.attempts < 2 or args.attempts > 20:
        raise SystemExit("--attempts must be between 2 and 20")
    try:
        audit = audit_backend(
            args.backend_url,
            brute_force=args.brute_force,
            attempts=args.attempts,
        )
        if args.frontend_url:
            audit_frontend(args.frontend_url, audit)
    except (OSError, URLError) as exc:
        print(f"ERROR: audit could not reach deployment: {exc}", file=sys.stderr)
        return 2

    for name in audit.passes:
        print(f"PASS {name}")
    for failure in audit.failures:
        print(f"FAIL {failure}", file=sys.stderr)
    print(f"Summary: {len(audit.passes)} passed, {len(audit.failures)} failed")
    return 1 if audit.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
