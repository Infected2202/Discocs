"""URL vetting: what the bot is allowed to fetch at all."""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.pop("bot", None)

from bot.utils.links import UnsafeLinkError, find_first_url, validate_public_url


def public(_host: str) -> list[str]:
    return ["93.184.216.34"]


def internal(_host: str) -> list[str]:
    return ["192.168.1.41"]


def unresolvable(_host: str) -> list[str]:
    return []


def test_find_first_url_picks_the_url_out_of_a_sentence():
    assert find_first_url("глянь https://youtu.be/abc123 огонь") == "https://youtu.be/abc123"


def test_find_first_url_drops_trailing_punctuation():
    assert find_first_url("вот https://youtu.be/abc123.") == "https://youtu.be/abc123"


def test_find_first_url_ignores_plain_text():
    assert find_first_url("просто текст без ссылок") is None
    assert find_first_url(None) is None


def test_public_url_passes():
    url = "https://www.youtube.com/watch?v=abc"

    assert validate_public_url(url, resolver=public) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8711/api/v1/settings",
        "https://10.0.0.5/secret.mp3",
        "http://192.168.1.41:5000/v2/",
        "http://[::1]/x",
        "http://169.254.169.254/latest/meta-data/",
    ],
)
def test_literal_internal_addresses_are_refused(url: str):
    with pytest.raises(UnsafeLinkError):
        validate_public_url(url, resolver=public)


def test_hostname_resolving_inside_the_network_is_refused():
    # The whole point of resolving: nas.local and 127.0.0.1.nip.io look public.
    with pytest.raises(UnsafeLinkError):
        validate_public_url("https://nas.local/track.mp3", resolver=internal)


def test_unresolvable_host_is_refused():
    with pytest.raises(UnsafeLinkError):
        validate_public_url("https://nowhere.invalid/x", resolver=unresolvable)


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://example.com/x.mp3", "javascript:alert(1)"],
)
def test_non_http_schemes_are_refused(url: str):
    with pytest.raises(UnsafeLinkError):
        validate_public_url(url, resolver=public)


def test_absurdly_long_url_is_refused():
    with pytest.raises(UnsafeLinkError):
        validate_public_url("https://example.com/" + "a" * 5000, resolver=public)
