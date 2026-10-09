"""Устройства со своим ключом вместо общего DISCOCS_SERVICE_TOKEN (docs/auth.md, «Устройства»).

Инструмент на ПК придумывает ключ сам и просит доступ — запрос открыт без входа и только ставит устройство
в очередь. Подключает его человек в админке (раздел Access); машина (общий токен или другое устройство)
решать за людей не может.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app import auth
from app.api.deps import api_error, context
from app.schemas.requests import DeviceAccessRequest
from app.store.devices import TooManyPendingDevices

router = APIRouter(prefix="/api/v1")

DEVICE_KEY_HEADER = "X-Discocs-Device-Key"
DEVICE_REQUESTS_PATH = "/api/v1/devices/requests"
# Ключ придумывает устройство: secrets.token_hex(32) — 64 символа; короче — не ключ.
_KEY_MIN, _KEY_MAX = 32, 256
_NOT_FOUND = "Device not found"


def device_key(request: Request) -> str | None:
    key = request.headers.get(DEVICE_KEY_HEADER, "").strip()
    if not _KEY_MIN <= len(key) <= _KEY_MAX or not key.isascii() or not key.isprintable():
        return None
    return key


def client_ip(request: Request) -> str:
    from app.api.auth import _client_ip  # noqa: PLC0415 — та же логика доверенных прокси, что у входа

    return _client_ip(request)


def _decider(request: Request) -> str | None | JSONResponse:
    """Кто решает: человек с сессией (или кто угодно, пока гейт выключен), но не машина."""
    principal = getattr(request.state, "principal", None)
    if principal == "service" or (isinstance(principal, str) and principal.startswith("device:")):
        return api_error(403, "machine_principal", "Devices are approved by a person signed in to discocs")
    return principal


@router.post(DEVICE_REQUESTS_PATH.removeprefix("/api/v1"), response_model=None)
async def api_v1_device_request(request: Request, body: DeviceAccessRequest) -> dict[str, object] | JSONResponse:
    key = device_key(request)
    if key is None:
        return api_error(400, "device_key_required", f"{DEVICE_KEY_HEADER} header with a random key is required")
    store, _settings = context()
    try:
        device = await run_in_threadpool(
            store.request_device, auth.hash_token(key), name=body.name, kind=body.kind, info=body.info,
            ip=client_ip(request),
        )
    except TooManyPendingDevices:
        return api_error(429, "too_many_requests", "Too many devices are waiting for approval")
    return {"id": device["id"], "status": device["status"], "fingerprint": device["fingerprint"]}


@router.get("/devices")
def api_v1_devices() -> dict[str, object]:
    store, _settings = context()
    return {"devices": store.devices()}


def _decide(request: Request, device_id: int, decision: str) -> dict[str, object] | JSONResponse:
    decider = _decider(request)
    if isinstance(decider, JSONResponse):
        return decider
    store, _settings = context()
    if store.device(device_id) is None:
        return api_error(404, "not_found", _NOT_FOUND)
    return {"device": store.decide_device(device_id, decision, by=decider)}


@router.post("/devices/{device_id}/approve", response_model=None)
def api_v1_device_approve(request: Request, device_id: int) -> dict[str, object] | JSONResponse:
    return _decide(request, device_id, "approve")


@router.post("/devices/{device_id}/reject", response_model=None)
def api_v1_device_reject(request: Request, device_id: int) -> dict[str, object] | JSONResponse:
    return _decide(request, device_id, "reject")


@router.post("/devices/{device_id}/revoke", response_model=None)
def api_v1_device_revoke(request: Request, device_id: int) -> dict[str, object] | JSONResponse:
    return _decide(request, device_id, "revoke")


@router.delete("/devices/{device_id}", response_model=None)
def api_v1_device_delete(request: Request, device_id: int) -> dict[str, object] | JSONResponse:
    decider = _decider(request)
    if isinstance(decider, JSONResponse):
        return decider
    store, _settings = context()
    if not store.delete_device(device_id):
        return api_error(404, "not_found", _NOT_FOUND)
    return {"deleted": True}
