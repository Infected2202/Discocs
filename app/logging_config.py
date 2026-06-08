from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import Settings


DEFAULT_LOG_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_LOG_BACKUP_COUNT = 5
ANALYSIS_LOGGER_NAME = "discocs.analysis"
NAVIDROME_PLUGIN_LOGGER_NAME = "discocs.navidrome.plugin"
_CONFIGURED = False


def configure_logging(settings: Settings | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    settings = settings or Settings.from_env()
    log_dir = Path(os.getenv("DISCOCS_LOG_DIR", str(settings.data_dir / "logs")))
    log_dir.mkdir(parents=True, exist_ok=True)
    level = _log_level(os.getenv("DISCOCS_LOG_LEVEL", "INFO"))
    max_bytes = _positive_int(os.getenv("DISCOCS_LOG_MAX_BYTES"), DEFAULT_LOG_MAX_BYTES)
    backup_count = _positive_int(
        os.getenv("DISCOCS_LOG_BACKUP_COUNT"),
        DEFAULT_LOG_BACKUP_COUNT,
    )
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)
    _add_rotating_handler(
        root,
        log_dir / "discocs.log",
        formatter,
        level,
        max_bytes,
        backup_count,
        "discocs-main-file",
    )

    analysis_logger = get_analysis_logger()
    analysis_logger.setLevel(level)
    analysis_logger.propagate = True
    _add_rotating_handler(
        analysis_logger,
        log_dir / "analysis.log",
        formatter,
        level,
        max_bytes,
        backup_count,
        "discocs-analysis-file",
    )

    navidrome_plugin_logger = get_navidrome_plugin_logger()
    navidrome_plugin_logger.setLevel(level)
    navidrome_plugin_logger.propagate = False
    _add_rotating_handler(
        navidrome_plugin_logger,
        log_dir / "navidrome_plugin.log",
        formatter,
        level,
        max_bytes,
        backup_count,
        "discocs-navidrome-plugin-file",
    )
    _CONFIGURED = True


def get_analysis_logger() -> logging.Logger:
    return logging.getLogger(ANALYSIS_LOGGER_NAME)


def get_navidrome_plugin_logger() -> logging.Logger:
    return logging.getLogger(NAVIDROME_PLUGIN_LOGGER_NAME)


def _add_rotating_handler(
    logger: logging.Logger,
    path: Path,
    formatter: logging.Formatter,
    level: int,
    max_bytes: int,
    backup_count: int,
    marker: str,
) -> None:
    if any(getattr(handler, "_discocs_marker", None) == marker for handler in logger.handlers):
        return
    handler = RotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)
    handler._discocs_marker = marker  # type: ignore[attr-defined]
    logger.addHandler(handler)


def _log_level(value: str) -> int:
    level = getattr(logging, value.upper(), None)
    if isinstance(level, int):
        return level
    return logging.INFO


def _positive_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default
