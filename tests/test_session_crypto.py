"""Session-bound Navidrome credential encryption."""
from __future__ import annotations

import pytest
from cryptography.exceptions import InvalidTag

from app.session_crypto import decrypt_nav_secret, encrypt_nav_secret


def test_nav_secret_round_trip_requires_exact_raw_token():
    secret = encrypt_nav_secret("raw-session-token", "pāssword")

    assert secret.startswith("v1.")
    assert "pāssword" not in secret
    assert decrypt_nav_secret("raw-session-token", secret) == "pāssword"

    with pytest.raises(InvalidTag):
        decrypt_nav_secret("different-token", secret)


def test_nav_secret_uses_fresh_nonce_for_each_encryption():
    first = encrypt_nav_secret("token", "same-password")
    second = encrypt_nav_secret("token", "same-password")

    assert first != second
    assert decrypt_nav_secret("token", first) == "same-password"
    assert decrypt_nav_secret("token", second) == "same-password"
