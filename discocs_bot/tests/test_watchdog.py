from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.utils.watchdog import Watchdog


def _watchdog(tmp_path: Path, probe, **kwargs) -> Watchdog:
    return Watchdog(probe, tmp_path / "heartbeat", interval=0.0, timeout=0.5, **kwargs)


def test_successful_probe_writes_heartbeat(tmp_path: Path):
    async def alive() -> str:
        return "ok"

    watchdog = _watchdog(tmp_path, alive)
    heartbeat = tmp_path / "heartbeat"

    assert asyncio.run(watchdog.tick()) is True
    assert heartbeat.exists()
    assert heartbeat.read_text(encoding="utf-8").endswith("Z")
    assert watchdog.failures == 0


def test_failed_probe_counts_and_leaves_heartbeat_stale(tmp_path: Path):
    async def unreachable() -> None:
        raise OSError("[Errno -3] Temporary failure in name resolution")

    watchdog = _watchdog(tmp_path, unreachable, max_failures=3)

    assert asyncio.run(watchdog.tick()) is False
    assert watchdog.failures == 1
    # Heartbeat не обновляется при неудаче — иначе healthcheck врал бы о живом боте.
    assert not (tmp_path / "heartbeat").exists()
    assert watchdog.is_dead is False


def test_hanging_probe_is_a_failure(tmp_path: Path):
    """Туннель чаще не отказывает, а молчит — это тоже недоступность."""

    async def hangs() -> None:
        await asyncio.sleep(10)

    watchdog = Watchdog(hangs, tmp_path / "heartbeat", interval=0.0, timeout=0.05)

    assert asyncio.run(watchdog.tick()) is False
    assert watchdog.failures == 1


def test_recovery_resets_the_counter(tmp_path: Path):
    """Короткий обрыв не должен копиться до перезапуска."""
    outcomes = iter([False, False, True])

    async def flaky() -> str:
        if next(outcomes):
            return "ok"
        raise OSError("tunnel down")

    watchdog = _watchdog(tmp_path, flaky, max_failures=3)

    async def scenario() -> None:
        await watchdog.tick()
        await watchdog.tick()
        assert watchdog.failures == 2
        assert watchdog.is_dead is False
        await watchdog.tick()

    asyncio.run(scenario())

    assert watchdog.failures == 0
    assert watchdog.is_dead is False
    assert (tmp_path / "heartbeat").exists()


def test_run_sets_dead_after_max_failures(tmp_path: Path):
    calls = 0

    async def unreachable() -> None:
        nonlocal calls
        calls += 1
        raise OSError("tunnel down")

    watchdog = _watchdog(tmp_path, unreachable, max_failures=3)

    async def scenario() -> bool:
        dead = asyncio.Event()
        await asyncio.wait_for(watchdog.run(dead), timeout=5)
        return dead.is_set()

    assert asyncio.run(scenario()) is True
    assert calls == 3
    assert watchdog.is_dead is True


def test_run_keeps_going_while_telegram_answers(tmp_path: Path):
    """Живой Telegram не должен приводить к выходу — сколько бы проб ни прошло."""
    calls = 0

    async def alive() -> str:
        nonlocal calls
        calls += 1
        if calls >= 5:
            raise asyncio.CancelledError
        return "ok"

    watchdog = _watchdog(tmp_path, alive, max_failures=2)

    async def scenario() -> bool:
        dead = asyncio.Event()
        with pytest.raises(asyncio.CancelledError):
            await watchdog.run(dead)
        return dead.is_set()

    assert asyncio.run(scenario()) is False
    assert watchdog.failures == 0


def test_run_touches_heartbeat_before_first_probe(tmp_path: Path):
    """Иначе healthcheck считал бы бота мёртвым весь первый интервал."""

    async def slow_first_probe() -> None:
        await asyncio.sleep(10)

    watchdog = Watchdog(slow_first_probe, tmp_path / "heartbeat", interval=5.0, timeout=5.0)

    async def scenario() -> None:
        task = asyncio.create_task(watchdog.run(asyncio.Event()))
        await asyncio.sleep(0)  # дать run() дойти до первого touch
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert (tmp_path / "heartbeat").exists()


def test_unwritable_heartbeat_does_not_kill_the_bot(tmp_path: Path):
    """Сломанный heartbeat — повод показать unhealthy, а не ронять процесс."""
    blocked = tmp_path / "heartbeat"
    blocked.mkdir()  # запись по этому пути обречена: там каталог

    async def alive() -> str:
        return "ok"

    watchdog = Watchdog(alive, blocked, interval=0.0, timeout=0.5)

    assert asyncio.run(watchdog.tick()) is True
    assert watchdog.failures == 0
