from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import auth
from app.api import auth as auth_api
from app.api.auth import _client_ip
from app.main import app
from app.store import INITIALIZED_DB_PATHS, Store


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _reset_limiter():
    auth_api._limiter._failures.clear()
    yield
    auth_api._limiter._failures.clear()


def _enable_gate(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "app.db"
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    monkeypatch.setenv("DISCOCS_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("DISCOCS_AUTH_ENABLED", "true")
    monkeypatch.setenv("DISCOCS_NAVIDROME_URL", "http://navidrome:4533")
    Store(db_path).init()


def test_client_ip_uses_proxy_appended_last_hop(monkeypatch):
    monkeypatch.setenv("DISCOCS_TRUSTED_PROXY_CIDRS", "172.16.0.0/12")
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "headers": [
                (b"x-forwarded-for", b"198.51.100.99, 203.0.113.7"),
            ],
            "client": ("172.20.0.2", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )

    assert _client_ip(request) == "203.0.113.7"


def test_direct_lan_client_cannot_forge_forwarded_ip(monkeypatch):
    monkeypatch.setenv("DISCOCS_TRUSTED_PROXY_CIDRS", "172.16.0.0/12")
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "headers": [(b"x-forwarded-for", b"203.0.113.99")],
            "client": ("192.168.1.55", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )

    assert _client_ip(request) == "192.168.1.55"


def test_rotating_forwarded_ip_cannot_bypass_account_limiter(tmp_path, monkeypatch):
    _enable_gate(tmp_path, monkeypatch)
    monkeypatch.setattr(
        auth_api, "_client_ip", lambda request: request.headers["x-forwarded-for"]
    )

    monkeypatch.setenv("DISCOCS_LOGIN_MAX_ATTEMPTS", "3")
    monkeypatch.setattr(auth, "verify_navidrome_credentials", lambda *_args, **_kwargs: False)
    client = TestClient(app)
    payload = {"username": "target-account", "password": "wrong"}

    for suffix in range(1, 4):
        response = client.post(
            "/api/v1/auth/login",
            json=payload,
            headers={"X-Forwarded-For": f"203.0.113.{suffix}"},
        )
        assert response.status_code == 401

    locked = client.post(
        "/api/v1/auth/login",
        json=payload,
        headers={"X-Forwarded-For": "203.0.113.200"},
    )
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "too_many_attempts"


def test_login_rejects_control_characters_and_unknown_fields(tmp_path, monkeypatch):
    _enable_gate(tmp_path, monkeypatch)
    client = TestClient(app)

    injected = client.post(
        "/api/v1/auth/login",
        json={"username": "alice\nforged-log-line", "password": "wrong"},
    )
    assert injected.status_code == 422

    unexpected = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "wrong", "admin": True},
    )
    assert unexpected.status_code == 422


def test_cross_origin_state_change_is_rejected(tmp_path, monkeypatch):
    _enable_gate(tmp_path, monkeypatch)
    monkeypatch.setattr(auth, "verify_navidrome_credentials", lambda *_args, **_kwargs: False)
    client = TestClient(app)
    payload = {"username": "nobody", "password": "wrong"}

    rejected = client.post(
        "/api/v1/auth/login",
        json=payload,
        headers={"Origin": "https://evil.example"},
    )
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "cross_origin_request"

    same_origin = client.post(
        "/api/v1/auth/login",
        json=payload,
        headers={"Origin": "http://testserver"},
    )
    assert same_origin.status_code == 401


def test_security_headers_cover_public_and_rejected_responses(tmp_path, monkeypatch):
    _enable_gate(tmp_path, monkeypatch)
    client = TestClient(app)

    for response in (
        client.get("/api/v1/auth/session"),
        client.get("/api/v1/dashboard"),
    ):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert response.headers["permissions-policy"] == (
            "camera=(), microphone=(), geolocation=()"
        )

    assert client.get("/api/v1/auth/session").headers["cache-control"] == "no-store"


def test_public_nginx_overwrites_untrusted_identity_headers():
    config = (ROOT / "deploy" / "nginx" / "default.conf.template").read_text(
        encoding="utf-8"
    )

    assert "$proxy_add_x_forwarded_for" not in config
    assert config.count("proxy_set_header X-Forwarded-For $remote_addr;") == 5
    assert config.count('proxy_set_header X-Discocs-Service-Token "";') == 5
    assert "Content-Security-Policy" in config
    assert "script-src 'self' 'wasm-unsafe-eval'" in config
    assert "'unsafe-eval'" not in config


def test_prod_backend_declares_trusted_docker_proxy_cidr():
    compose = (ROOT / "deploy" / "prod" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )

    assert "DISCOCS_TRUSTED_PROXY_CIDRS:" in compose
    assert "172.16.0.0/12" in compose


def test_authenticated_startup_does_not_enable_wildcard_cors():
    main_source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")

    assert 'elif os.getenv("DISCOCS_AUTH_ENABLED"' in main_source
    assert 'allow_origins=["*"]' in main_source
