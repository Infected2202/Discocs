import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from telegram.error import NetworkError, TimedOut

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_ATTEMPTS = 4
DEFAULT_BASE_DELAY = 2.0


async def telegram_retry(
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    description: str = "telegram request",
) -> T:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except (TimedOut, NetworkError) as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            delay = base_delay * attempt
            logger.warning(
                "%s failed (attempt %s/%s): %s; retry in %.1fs",
                description,
                attempt,
                attempts,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc
