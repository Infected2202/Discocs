"""Share shortlinks.

The Share button in the SoundCloud app hands out on.soundcloud.com/xxxx, which
no extractor claims — it is a redirect stub. Following it needs care: a public
shortener can bounce the request anywhere, including inside our own network.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.pop("bot", None)

from bot.services import external_audio as external_audio_module
from bot.services.external_audio import ExternalAudioError, LinkAudioService
from bot.utils.links import validate_public_url

SHORT = "https://on.soundcloud.com/AbCdEf"
REAL = "https://soundcloud.com/artist/track"


def settings() -> SimpleNamespace:
    return SimpleNamespace(
        ytdlp_cookies_file="",
        external_max_duration_minutes=180,
        external_max_download_bytes=500 * 1024 * 1024,
        external_bitrate_headroom=1.0,
        external_max_bitrate_kbps=320,
    )


def offline_validation(monkeypatch) -> None:
    """Keep the real URL rules but resolve names without touching DNS.

    Literal addresses are still judged for what they are, so the "redirect into
    the network" case below exercises the real check.
    """
    monkeypatch.setattr(
        external_audio_module,
        "validate_public_url",
        lambda url: validate_public_url(url, resolver=lambda host: ["93.184.216.34"]),
    )


def service(monkeypatch, hops: dict[str, str], supported: set[str]) -> LinkAudioService:
    """A service whose extractor set and redirect map are both fixed."""
    offline_validation(monkeypatch)
    instance = LinkAudioService(settings())

    def handler(request: httpx.Request) -> httpx.Response:
        target = hops.get(str(request.url))
        if target is None:
            return httpx.Response(200)
        return httpx.Response(301, headers={"location": target})

    instance._http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    )
    monkeypatch.setattr(instance, "is_supported", lambda url: url in supported)
    return instance


def test_a_supported_url_is_returned_untouched(monkeypatch):
    instance = service(monkeypatch, hops={}, supported={REAL})

    assert asyncio.run(instance.resolve(REAL)) == REAL


def test_shortlink_is_followed_to_the_real_url(monkeypatch):
    instance = service(monkeypatch, hops={SHORT: REAL}, supported={REAL})

    assert asyncio.run(instance.resolve(SHORT)) == REAL


def test_relative_redirect_is_resolved_against_the_current_url(monkeypatch):
    instance = service(
        monkeypatch,
        hops={"https://snd.sc/x": "/artist/track"},
        supported={"https://snd.sc/artist/track"},
    )

    assert asyncio.run(instance.resolve("https://snd.sc/x")) == "https://snd.sc/artist/track"


def test_redirect_into_the_network_is_refused(monkeypatch):
    # The whole reason redirects are followed one hop at a time.
    instance = service(
        monkeypatch,
        hops={SHORT: "http://192.168.1.41:8711/api/v1/settings"},
        supported=set(),
    )

    with pytest.raises(ExternalAudioError):
        asyncio.run(instance.resolve(SHORT))


def test_a_redirect_chain_cannot_loop_forever(monkeypatch):
    instance = service(
        monkeypatch,
        hops={"https://a.example/1": "https://a.example/1"},
        supported=set(),
    )

    assert asyncio.run(instance.resolve("https://a.example/1")) == "https://a.example/1"


def test_an_unreachable_shortlink_falls_back_to_the_original(monkeypatch):
    offline_validation(monkeypatch)
    instance = LinkAudioService(settings())

    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=request)

    instance._http = httpx.AsyncClient(
        transport=httpx.MockTransport(explode),
        follow_redirects=False,
    )
    monkeypatch.setattr(instance, "is_supported", lambda url: False)

    # fetch_info then reports "unknown source", which is the honest answer.
    assert asyncio.run(instance.resolve(SHORT)) == SHORT
