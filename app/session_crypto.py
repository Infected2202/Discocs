"""Encrypt Navidrome session credentials with the raw opaque session token."""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_AAD = b"discocs-nav-secret-v1"
_PREFIX = "v1."
_NONCE_BYTES = 12


def encrypt_nav_secret(token: str, password: str) -> str:
    """Return an authenticated ciphertext that cannot be opened without token."""
    if not token or not password:
        raise ValueError("token and password are required")
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(_derive_key(token)).encrypt(
        nonce,
        password.encode("utf-8"),
        _AAD,
    )
    payload = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")
    return _PREFIX + payload


def decrypt_nav_secret(token: str, secret: str) -> str:
    """Decrypt a v1 session secret; authentication failure is fail-closed."""
    if not token or not secret.startswith(_PREFIX):
        raise ValueError("unsupported session secret")
    raw = base64.urlsafe_b64decode(secret[len(_PREFIX) :].encode("ascii"))
    if len(raw) <= _NONCE_BYTES:
        raise ValueError("invalid session secret")
    plaintext = AESGCM(_derive_key(token)).decrypt(
        raw[:_NONCE_BYTES],
        raw[_NONCE_BYTES:],
        _AAD,
    )
    return plaintext.decode("utf-8")


def _derive_key(token: str) -> bytes:
    return hashlib.sha256(b"discocs-nav-key-v1\0" + token.encode("utf-8")).digest()
