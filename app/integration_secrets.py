"""Шифрование доступов к внешним сервисам (Beatport, Discogs) в базе discocs.

Ключ выводится из ``DISCOCS_SERVICE_TOKEN``: фоновая синхронизация лейблов идёт без сессии
пользователя, поэтому ключ сессий (``app/session_crypto.py``) ей не подходит. Сменили токен —
сохранённые доступы не расшифруются, их надо ввести в админке заново.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_AAD = b"discocs-integration-secret-v1"
_PREFIX = "i1."
_NONCE_BYTES = 12


class IntegrationSecretError(ValueError):
    """Нет серверного ключа или секрет не расшифровывается этим ключом."""


def encrypt_integration_secret(server_secret: str, value: dict[str, object]) -> str:
    if not server_secret:
        raise IntegrationSecretError("DISCOCS_SERVICE_TOKEN is not set: nothing to encrypt credentials with")
    nonce = os.urandom(_NONCE_BYTES)
    plaintext = json.dumps(value, ensure_ascii=False).encode("utf-8")
    ciphertext = AESGCM(_derive_key(server_secret)).encrypt(nonce, plaintext, _AAD)
    return _PREFIX + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt_integration_secret(server_secret: str, blob: str) -> dict[str, object]:
    if not server_secret:
        raise IntegrationSecretError("DISCOCS_SERVICE_TOKEN is not set: cannot read saved credentials")
    if not blob.startswith(_PREFIX):
        raise IntegrationSecretError("Unsupported credentials format")
    try:
        raw = base64.urlsafe_b64decode(blob[len(_PREFIX):].encode("ascii"))
    except ValueError as exc:
        raise IntegrationSecretError("Saved credentials are damaged") from exc
    if len(raw) <= _NONCE_BYTES:
        raise IntegrationSecretError("Saved credentials are damaged")
    try:
        plaintext = AESGCM(_derive_key(server_secret)).decrypt(raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], _AAD)
    except InvalidTag as exc:
        raise IntegrationSecretError("Saved credentials do not match DISCOCS_SERVICE_TOKEN") from exc
    value = json.loads(plaintext.decode("utf-8"))
    if not isinstance(value, dict):
        raise IntegrationSecretError("Saved credentials are damaged")
    return value


def _derive_key(server_secret: str) -> bytes:
    return hashlib.sha256(b"discocs-integration-key-v1\0" + server_secret.encode("utf-8")).digest()
