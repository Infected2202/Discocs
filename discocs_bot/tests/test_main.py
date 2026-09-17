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


async def _never_answers() -> None:
    """Проба сторожа, которая не возвращается — как при мёртвом туннеле."""
    await asyncio.Event().wait()


def _fake_settings(tmp_path: Path, **overrides) -> SimpleNamespace:
    defaults = dict(
        sqlite_path=Path("data") / "bot.sqlite",
        heartbeat_path=tmp_path / "heartbeat",
        watchdog_interval_seconds=60.0,
        watchdog_timeout_seconds=30.0,
        watchdog_max_failures=5,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_run_bot_reraises_cancelled_error_after_cleanup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    settings = _fake_settings(tmp_path)
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
            self.bot = SimpleNamespace(get_me=_never_answers)

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


def test_run_bot_exits_for_restart_when_watchdog_gives_up(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Глухая сеть должна заканчиваться выходом процесса, а не молчаливым Up."""
    settings = _fake_settings(
        tmp_path,
        watchdog_interval_seconds=0.0,
        watchdog_timeout_seconds=0.1,
        watchdog_max_failures=2,
    )
    events: list[str] = []

    async def unreachable() -> None:
        raise OSError("[Errno -3] Temporary failure in name resolution")

    class FakeUpdater:
        async def start_polling(self, *, drop_pending_updates: bool) -> None:
            events.append("start_polling")

    class FakeApplication:
        def __init__(self) -> None:
            self.updater = FakeUpdater()
            self.bot = SimpleNamespace(get_me=unreachable)

        async def initialize(self) -> None:
            pass

        async def start(self) -> None:
            pass

    async def fake_post_init(_application) -> None:
        pass

    async def fake_shutdown(_application) -> None:
        events.append("shutdown")

    guard_events: list[str] = []

    def fake_guard(*_args, **_kwargs) -> SimpleNamespace:
        guard_events.append("armed")
        return SimpleNamespace(cancel=lambda: guard_events.append("cancelled"))

    monkeypatch.setattr(bot_main, "get_settings", lambda: settings)
    monkeypatch.setattr(bot_main, "acquire", lambda path: None)
    monkeypatch.setattr(bot_main, "release", lambda path: events.append("release"))
    monkeypatch.setattr(bot_main, "build_application", lambda: FakeApplication())
    monkeypatch.setattr(bot_main, "_post_init", fake_post_init)
    monkeypatch.setattr(bot_main, "_shutdown", fake_shutdown)
    monkeypatch.setattr(bot_main, "_arm_shutdown_guard", fake_guard)

    assert asyncio.run(bot_main._run_bot()) is True
    assert events == ["start_polling", "shutdown", "release"]
    # Страховка от зависания в shutdown взводится и снимается, а не течёт.
    assert guard_events == ["armed", "cancelled"]


def _patch_main_loop(monkeypatch: pytest.MonkeyPatch, result: bool) -> None:
    monkeypatch.setattr(
        bot_main,
        "get_settings",
        lambda: SimpleNamespace(bot_token="123:real-looking-token"),
    )

    def fake_run(coro):
        coro.close()  # корутину не выполняем — закрываем, чтобы не текла
        return result

    monkeypatch.setattr(bot_main.asyncio, "run", fake_run)


def test_main_raises_system_exit_when_run_bot_asks_for_restart(monkeypatch: pytest.MonkeyPatch):
    """Супервизор поднимет бота только по ненулевому коду выхода."""
    _patch_main_loop(monkeypatch, result=True)

    with pytest.raises(SystemExit) as excinfo:
        bot_main.main()

    assert excinfo.value.code == 1


def test_main_returns_quietly_when_bot_stops_normally(monkeypatch: pytest.MonkeyPatch):
    _patch_main_loop(monkeypatch, result=False)

    bot_main.main()
