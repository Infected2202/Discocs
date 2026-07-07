from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.pop("bot.main", None)
sys.modules.pop("bot", None)

from bot import main as bot_main


def test_run_bot_reraises_cancelled_error_after_cleanup(monkeypatch: pytest.MonkeyPatch):
    settings = SimpleNamespace(sqlite_path=Path("data") / "bot.sqlite")
    events: list[str] = []
    started = asyncio.Event()

    class FakeUpdater:
        async def start_polling(self, *, drop_pending_updates: bool) -> None:
            assert drop_pending_updates is True
            events.append("start_polling")
            started.set()

    class FakeApplication:
        def __init__(self) -> None:
            self.updater = FakeUpdater()

        async def initialize(self) -> None:
            events.append("initialize")

        async def start(self) -> None:
            events.append("start")

    async def fake_post_init(_application) -> None:
        events.append("post_init")

    async def fake_shutdown(_application) -> None:
        events.append("shutdown")

    monkeypatch.setattr(bot_main, "get_settings", lambda: settings)
    monkeypatch.setattr(bot_main, "acquire", lambda path: events.append(f"acquire:{path}"))
    monkeypatch.setattr(bot_main, "release", lambda path: events.append(f"release:{path}"))
    monkeypatch.setattr(bot_main, "build_application", lambda: FakeApplication())
    monkeypatch.setattr(bot_main, "_post_init", fake_post_init)
    monkeypatch.setattr(bot_main, "_shutdown", fake_shutdown)

    async def run_and_cancel() -> None:
        task = asyncio.create_task(bot_main._run_bot())
        await started.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return
        raise AssertionError("CancelledError was not re-raised")

    asyncio.run(run_and_cancel())

    assert events == [
        "acquire:data",
        "initialize",
        "post_init",
        "start",
        "start_polling",
        "shutdown",
        "release:data",
    ]
