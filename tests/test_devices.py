"""Устройства со своим ключом вместо общего DISCOCS_SERVICE_TOKEN (docs/auth.md, «Устройства»)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from app import auth
from app import cli as cli_module
from app.main import app
from app.store import INITIALIZED_DB_PATHS, Store
from app.store.devices import MAX_PENDING_DEVICES, TooManyPendingDevices

KEY = "a" * 64
OTHER_KEY = "b" * 64
PROTECTED = "/api/v1/settings/navidrome"


def init_store(tmp_path: Path, monkeypatch) -> Store:
    db_path = tmp_path / "app.db"
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    monkeypatch.setenv("DISCOCS_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_MODEL_DIR", str(tmp_path / "models"))
    store = Store(db_path)
    store.init()
    return store


def enable_gate(monkeypatch) -> None:
    monkeypatch.setenv("DISCOCS_AUTH_ENABLED", "true")
    monkeypatch.setenv("DISCOCS_NAVIDROME_URL", "http://navidrome:4533")
    monkeypatch.setenv("DISCOCS_SERVICE_TOKEN", "svc-secret")
    monkeypatch.setattr(auth, "verify_navidrome_credentials",
                        lambda settings, username, password, **kwargs: (username, password) == ("alice", "correct"))
    monkeypatch.setattr(auth, "sync_navidrome_starred_for_user", lambda *_args, **_kwargs: None)


def ask(client: TestClient, key: str = KEY, name: str = "nexuspc · Tools worker"):
    return client.post("/api/v1/devices/requests", headers={"X-Discocs-Device-Key": key},
                       json={"name": name, "kind": "tools-agent", "info": {"host": "nexuspc"}})


def signed_in(monkeypatch) -> TestClient:
    admin = TestClient(app)
    assert admin.post("/api/v1/auth/login", json={"username": "alice", "password": "correct"}).status_code == 200
    return admin


# --- хранилище ------------------------------------------------------------------


def test_repeated_request_with_the_same_key_is_the_same_device(tmp_path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)

    first = store.request_device("h1", name="pc · worker", kind="tools-agent", info={"v": 1}, ip="10.0.0.2")
    again = store.request_device("h1", name="pc · worker 2", kind="tools-agent", info={"v": 2}, ip="10.0.0.3")

    assert again["id"] == first["id"]
    assert (again["status"], again["name"], again["info"]) == ("pending", "pc · worker 2", {"v": 2})
    assert len(store.devices()) == 1


def test_pending_requests_are_capped_and_stale_ones_forgotten(tmp_path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    for index in range(MAX_PENDING_DEVICES):
        store.request_device(f"h{index}", name=f"d{index}", kind="tools-agent", info={}, ip=None)

    with pytest.raises(TooManyPendingDevices):
        store.request_device("one-too-many", name="x", kind="tools-agent", info={}, ip=None)

    # Неделю без решения — запрос забыт, место освобождается.
    old = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    with store.connect() as conn:
        conn.execute("UPDATE device_keys SET created_at = ? WHERE key_hash = 'h0'", (old,))
    assert store.request_device("one-too-many", name="x", kind="tools-agent", info={}, ip=None)["status"] == "pending"
    assert all(device["fingerprint"] != "h0" for device in store.devices())


# --- гейт и API -----------------------------------------------------------------


def test_device_key_works_only_after_a_person_approves_it(tmp_path, monkeypatch):
    store = init_store(tmp_path, monkeypatch)
    enable_gate(monkeypatch)
    device = TestClient(app)
    headers = {"X-Discocs-Device-Key": KEY}

    # Неизвестный ключ — понятный отказ, а не просто 401.
    unknown = device.get(PROTECTED, headers=headers)
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (401, "device_unknown")

    # Попросить доступ можно без входа; ключ на сервере не хранится, только его отпечаток.
    asked = ask(device)
    assert asked.status_code == 200
    assert asked.json()["status"] == "pending"
    assert asked.json()["fingerprint"] == auth.hash_token(KEY)[:8]
    pending = device.get(PROTECTED, headers=headers)
    assert (pending.status_code, pending.json()["error"]["code"]) == (401, "device_pending")

    admin = signed_in(monkeypatch)
    listed = admin.get("/api/v1/devices").json()["devices"]
    assert [(item["name"], item["status"]) for item in listed] == [("nexuspc · Tools worker", "pending")]
    assert "key_hash" not in listed[0]
    approved = admin.post(f"/api/v1/devices/{asked.json()['id']}/approve").json()["device"]
    assert (approved["status"], approved["decided_by"]) == ("approved", "alice")

    with store.connect() as conn:
        conn.execute("UPDATE device_keys SET last_seen_at = NULL")
    assert device.get(PROTECTED, headers=headers).status_code == 200
    assert store.devices()[0]["last_seen_at"] is not None  # админка видит, что устройство на связи

    admin.post(f"/api/v1/devices/{asked.json()['id']}/revoke")
    revoked = device.get(PROTECTED, headers=headers)
    assert (revoked.status_code, revoked.json()["error"]["code"]) == (401, "device_revoked")


def test_machines_cannot_approve_devices(tmp_path, monkeypatch):
    init_store(tmp_path, monkeypatch)
    enable_gate(monkeypatch)
    machine = TestClient(app)
    first = ask(machine, KEY).json()["id"]
    second = ask(machine, OTHER_KEY, name="stranger").json()["id"]
    signed_in(monkeypatch).post(f"/api/v1/devices/{first}/approve")

    # Подключённое устройство не может подключить другое, общий токен — тоже: решает человек.
    by_device = machine.post(f"/api/v1/devices/{second}/approve", headers={"X-Discocs-Device-Key": KEY})
    by_service = machine.post(f"/api/v1/devices/{second}/approve", headers={"X-Discocs-Service-Token": "svc-secret"})

    assert by_device.status_code == by_service.status_code == 403
    assert by_device.json()["error"]["code"] == "machine_principal"
    assert machine.get(PROTECTED, headers={"X-Discocs-Device-Key": OTHER_KEY}).status_code == 401


def test_request_needs_a_real_key_and_a_deleted_device_can_ask_again(tmp_path, monkeypatch):
    init_store(tmp_path, monkeypatch)
    enable_gate(monkeypatch)
    device = TestClient(app)
    assert ask(device, "short").status_code == 400

    asked = ask(device).json()
    admin = signed_in(monkeypatch)
    admin.post(f"/api/v1/devices/{asked['id']}/reject")
    assert ask(device).json()["status"] == "rejected"  # спрашивать снова бесполезно, пока не удалят
    assert admin.delete(f"/api/v1/devices/{asked['id']}").json() == {"deleted": True}

    again = ask(device).json()
    assert again["status"] == "pending"
    assert again["id"] != asked["id"]


def test_admin_has_an_access_section_wired_to_the_devices_api():
    page = TestClient(app).get("/admin").text

    assert """data-nav="access" onclick="showSection('access')\"""" in page
    assert '<section id="access" class="section">' in page
    assert 'toolsApi("/api/v1/devices")' in page
    assert "/api/v1/devices/${deviceId}/${action}" in page


# --- GPU-воркер анализа ---------------------------------------------------------


def test_worker_without_service_token_uses_its_own_persistent_key(tmp_path, monkeypatch):
    monkeypatch.delenv("DISCOCS_SERVICE_TOKEN", raising=False)
    monkeypatch.setattr(cli_module, "_DEVICE_KEY", None)

    key = cli_module.load_worker_device_key(tmp_path)

    assert cli_module._service_headers() == {"X-Discocs-Device-Key": key}
    assert cli_module.load_worker_device_key(tmp_path) == key  # тот же ключ после перезапуска
    monkeypatch.setenv("DISCOCS_SERVICE_TOKEN", "svc-secret")
    assert cli_module._service_headers() == {"X-Discocs-Service-Token": "svc-secret"}


def test_worker_asks_for_access_when_not_let_in_and_registers_once_approved(monkeypatch):
    monkeypatch.setattr(cli_module, "_DEVICE_KEY", KEY)
    monkeypatch.setattr(cli_module.time, "sleep", lambda _seconds: None)
    answers = iter([HTTPError("u", 401, "Unauthorized", None, None),  # type: ignore[arg-type]
                    HTTPError("u", 401, "Unauthorized", None, None),  # type: ignore[arg-type]
                    {"ok": True}])
    calls: list[str] = []

    def fake_post_json(server, path, payload, **kwargs):
        calls.append(path)
        if path == "/api/v1/devices/requests":
            assert payload["kind"] == "analysis-worker"
            return {"id": 1, "status": "pending", "fingerprint": "abcd1234"}
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(cli_module, "post_json", fake_post_json)

    cli_module.register_worker_with_retry("http://server", "gpu-1", ["discogs_multi"], 5.0, False)

    assert calls == ["/api/v1/workers/register", "/api/v1/devices/requests",
                     "/api/v1/workers/register", "/api/v1/devices/requests",
                     "/api/v1/workers/register"]
