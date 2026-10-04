"""Доступы синхронизации лейблов: вход в Beatport и токен Discogs, зашифрованные в базе."""
from __future__ import annotations

import time

import httpx

from app.config import Settings
from app.integration_secrets import (
    IntegrationSecretError,
    decrypt_integration_secret,
    encrypt_integration_secret,
)
from app.services.label_sync.clients import beatport_login
from app.store import Store

BEATPORT_SECRET = "beatport"
DISCOGS_SECRET = "discogs"


def _server_secret(settings: Settings) -> str:
    return settings.auth.service_token


def load_secret(store: Store, settings: Settings, name: str) -> dict[str, object] | None:
    blob = store.get_integration_secret(name)
    if blob is None:
        return None
    return decrypt_integration_secret(_server_secret(settings), blob)


def save_secret(store: Store, settings: Settings, name: str, value: dict[str, object]) -> None:
    store.set_integration_secret(name, encrypt_integration_secret(_server_secret(settings), value))


def connect_beatport(store: Store, settings: Settings, http: httpx.Client, username: str, password: str) -> None:
    token = beatport_login(http, username, password)
    save_secret(store, settings, BEATPORT_SECRET, {**token, "username": username})


def set_discogs_token(store: Store, settings: Settings, token: str) -> None:
    save_secret(store, settings, DISCOGS_SECRET, {"token": token})


def credentials_status(store: Store, settings: Settings) -> dict[str, object]:
    """Что видно в админке: подключено ли, под кем, — без самих токенов."""
    status: dict[str, object] = {"server_key": bool(_server_secret(settings))}
    try:
        beatport = load_secret(store, settings, BEATPORT_SECRET)
        status["beatport"] = (
            {"connected": True, "username": beatport.get("username"),
             "token_expires_in": max(0, int(float(beatport.get("expires_at") or 0) - time.time()))}
            if beatport else {"connected": False}
        )
    except IntegrationSecretError as exc:
        status["beatport"] = {"connected": False, "error": str(exc)}
    try:
        discogs = load_secret(store, settings, DISCOGS_SECRET)
        status["discogs"] = {"connected": bool(discogs and discogs.get("token"))}
    except IntegrationSecretError as exc:
        status["discogs"] = {"connected": False, "error": str(exc)}
    return status
