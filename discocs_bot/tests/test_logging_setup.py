"""Log hygiene."""
from __future__ import annotations

import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.pop("bot", None)

import bot.utils.logging  # noqa: F401  (importing it is what configures logging)


def test_httpx_does_not_log_request_urls():
    """Every Telegram API URL carries the bot token in its path.

    At INFO, httpx logs one line per request, so the token ends up in plain
    text in the container log for anyone who reads it.
    """
    assert logging.getLogger("httpx").level >= logging.WARNING
