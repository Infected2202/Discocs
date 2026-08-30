"""Posting external audio to the backend.

Regression tests for the failure that made every radio attempt report "Не
удалось построить радио": httpx builds a *sync* byte stream from a plain file
object, and sending that from an AsyncClient raises "Attempted to send an sync
request with an AsyncClient instance" before any bytes leave the process.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.pop("bot", None)

from bot.services.discocs import _stream_file


def test_stream_file_yields_the_whole_file(tmp_path: Path):
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"abcdefghij")

    async def collect() -> bytes:
        return b"".join([chunk async for chunk in _stream_file(path, chunk_size=3)])

    assert asyncio.run(collect()) == b"abcdefghij"


def test_stream_file_handles_an_empty_file(tmp_path: Path):
    path = tmp_path / "empty.mp3"
    path.write_bytes(b"")

    async def collect() -> bytes:
        return b"".join([chunk async for chunk in _stream_file(path)])

    assert asyncio.run(collect()) == b""


def test_request_body_is_async_iterable(tmp_path: Path):
    """The property AsyncClient requires, and a file object does not have."""
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"x" * 32)

    request = httpx.Request("POST", "http://backend/api", content=_stream_file(path))

    assert hasattr(request.stream, "__aiter__")


def test_a_plain_file_object_would_not_be_async_iterable(tmp_path: Path):
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"x" * 32)

    with path.open("rb") as handle:
        request = httpx.Request("POST", "http://backend/api", content=handle)

    assert not hasattr(request.stream, "__aiter__")
