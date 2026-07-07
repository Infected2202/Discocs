import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.pop("bot.services", None)
sys.modules.pop("bot.services.navidrome", None)
sys.modules.pop("bot", None)

import bot.services.navidrome as navidrome_module


class _FakeStreamResponse:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    def raise_for_status(self) -> None:
        return None

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class _FakeStreamContext:
    def __init__(self, response: _FakeStreamResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeStreamResponse:
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeAsyncClient:
    def __init__(self, response: _FakeStreamResponse) -> None:
        self._response = response
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def stream(self, method: str, url: str, params: dict[str, str]) -> _FakeStreamContext:
        self.calls.append((method, url, params))
        return _FakeStreamContext(self._response)

    async def aclose(self) -> None:
        return None


def test_download_stream_offloads_file_io_to_thread(tmp_path: Path, monkeypatch):
    settings = SimpleNamespace(
        navidrome_base_url="http://navidrome:4533",
        navidrome_password="secret",
        navidrome_username="tester",
        navidrome_api_version="1.16.1",
        navidrome_client_name="discocs-bot",
    )
    client = navidrome_module.NavidromeClient(settings)
    fake_http = _FakeAsyncClient(_FakeStreamResponse([b"first-", b"second"]))
    client._client = fake_http

    to_thread_calls: list[str] = []

    async def fake_to_thread(func, /, *args, **kwargs):
        to_thread_calls.append(func.__name__)
        return func(*args, **kwargs)

    monkeypatch.setattr(
        navidrome_module,
        "asyncio",
        SimpleNamespace(to_thread=fake_to_thread),
        raising=False,
    )
    monkeypatch.setattr(
        navidrome_module,
        "secrets",
        SimpleNamespace(token_hex=lambda _size: "salt1234salt1234"),
        raising=False,
    )

    destination = tmp_path / "stream.bin"

    async def run_download() -> int:
        return await client.download_stream("song-1", destination, max_bit_rate=192)

    written = asyncio.run(run_download())

    assert written == len(b"first-second")
    assert destination.read_bytes() == b"first-second"
    assert to_thread_calls == ["open", "write", "write", "close"]
    assert fake_http.calls == [
        (
            "GET",
            "http://navidrome:4533/rest/stream.view",
            {
                "u": "tester",
                "t": "252da145d614be80fb6e14d7fead1e39",
                "s": "salt1234salt1234",
                "v": "1.16.1",
                "c": "discocs-bot",
                "f": "json",
                "id": "song-1",
                "maxBitRate": "192",
            },
        )
    ]
