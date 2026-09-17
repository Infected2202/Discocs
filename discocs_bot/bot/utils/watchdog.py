"""Сторож сетевой доступности Telegram.

Бот сидит в сетевом неймспейсе awg-туннеля (см. `deploy/prod/docker-compose.yml`).
Когда туннель падает, PTB не считает это фатальным: `network_retry_loop` ретраит
`get_updates` вечно, процесс живёт, `restart: unless-stopped` не срабатывает —
контейнер показывает `Up`, а бот молчит. 16.09.2026 так прошло полтора суток,
пока не пришёл пользователь.

Сторож превращает молчание в два наблюдаемых факта:

* **heartbeat-файл** — по его возрасту healthcheck контейнера видит «бот не
  отвечает» задолго до того, как это заметит человек;
* **выход процесса** после затяжной недоступности — по нему docker поднимает
  бота заново. Это единственный способ вернуть бота, когда awg-контейнер
  пересоздавался: старый сетевой неймспейс остаётся мёртвым навсегда, и
  переподключиться к новому можно только рестартом.

Проба идёт через тот же HTTP-клиент, которым бот отвечает пользователям, а не
через отдельный пул `get_updates` — сторож должен видеть ровно тот путь, по
которому ходят ответы.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 60.0
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_FAILURES = 5


class Watchdog:
    """Периодически проверяет Telegram и ведёт heartbeat-файл."""

    def __init__(
        self,
        probe: Callable[[], Awaitable[object]],
        heartbeat_path: Path,
        *,
        interval: float = DEFAULT_INTERVAL_SECONDS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_failures: int = DEFAULT_MAX_FAILURES,
    ) -> None:
        self._probe = probe
        self._heartbeat_path = Path(heartbeat_path)
        self._interval = interval
        self._timeout = timeout
        self._max_failures = max(1, max_failures)
        self._failures = 0

    @property
    def failures(self) -> int:
        return self._failures

    @property
    def is_dead(self) -> bool:
        return self._failures >= self._max_failures

    def touch(self) -> None:
        """Отметить, что Telegram отвечает. Healthcheck читает mtime файла."""
        try:
            self._heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
            self._heartbeat_path.write_text(
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                encoding="utf-8",
            )
        except OSError:
            # Сторож не должен ронять бота из-за проблем с файлом: не пишется
            # heartbeat — healthcheck покажет unhealthy, это и так верный сигнал.
            logger.warning("Could not write heartbeat to %s", self._heartbeat_path, exc_info=True)

    async def tick(self) -> bool:
        """Одна проба. True — Telegram ответил."""
        try:
            await asyncio.wait_for(self._probe(), timeout=self._timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._failures += 1
            logger.warning(
                "Watchdog: Telegram unreachable (%s/%s): %s",
                self._failures,
                self._max_failures,
                exc,
            )
            return False

        if self._failures:
            logger.info("Watchdog: Telegram reachable again after %s failed probe(s)", self._failures)
        self._failures = 0
        self.touch()
        return True

    async def run(self, dead: asyncio.Event) -> None:
        """Крутит пробы, пока Telegram не пропадёт насовсем; тогда ставит `dead`."""
        self.touch()
        while True:
            await asyncio.sleep(self._interval)
            await self.tick()
            if self.is_dead:
                logger.error(
                    "Watchdog: Telegram unreachable for %s probes in a row — exiting for a restart",
                    self._failures,
                )
                dead.set()
                return
