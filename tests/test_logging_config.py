from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from app.config import Settings
import app.logging_config as logging_config


def reset_discocs_logging() -> None:
    logging_config._CONFIGURED = False
    for logger in [
        logging.getLogger(),
        logging_config.get_analysis_logger(),
    ]:
        for handler in list(logger.handlers):
            if getattr(handler, "_discocs_marker", None):
                logger.removeHandler(handler)
                handler.close()


def test_configure_logging_creates_rotating_handlers(tmp_path, monkeypatch):
    reset_discocs_logging()
    monkeypatch.setenv("DISCOCS_LOG_DIR", str(tmp_path / "logs"))
    settings = Settings(
        data_dir=tmp_path,
        db_path=tmp_path / "app.db",
        model_dir=tmp_path / "models",
        index_dir=tmp_path,
    )

    logging_config.configure_logging(settings)

    assert (tmp_path / "logs").is_dir()
    root_handlers = [
        handler for handler in logging.getLogger().handlers
        if getattr(handler, "_discocs_marker", None) == "discocs-main-file"
    ]
    analysis_handlers = [
        handler for handler in logging_config.get_analysis_logger().handlers
        if getattr(handler, "_discocs_marker", None) == "discocs-analysis-file"
    ]
    assert len(root_handlers) == 1
    assert len(analysis_handlers) == 1
    assert isinstance(root_handlers[0], RotatingFileHandler)
    assert isinstance(analysis_handlers[0], RotatingFileHandler)

    logging_config.configure_logging(settings)

    assert len([
        handler for handler in logging.getLogger().handlers
        if getattr(handler, "_discocs_marker", None) == "discocs-main-file"
    ]) == 1
    assert len([
        handler for handler in logging_config.get_analysis_logger().handlers
        if getattr(handler, "_discocs_marker", None) == "discocs-analysis-file"
    ]) == 1
    reset_discocs_logging()


def test_analysis_logger_writes_analysis_log(tmp_path, monkeypatch):
    reset_discocs_logging()
    monkeypatch.setenv("DISCOCS_LOG_DIR", str(tmp_path / "logs"))
    settings = Settings(
        data_dir=tmp_path,
        db_path=tmp_path / "app.db",
        model_dir=tmp_path / "models",
        index_dir=tmp_path,
    )
    logging_config.configure_logging(settings)

    logging_config.get_analysis_logger().error("analysis failure marker")
    for handler in logging_config.get_analysis_logger().handlers:
        handler.flush()

    assert "analysis failure marker" in (tmp_path / "logs" / "analysis.log").read_text()
    reset_discocs_logging()


def test_share_capability_token_is_redacted_from_uvicorn_arguments():
    token = "A" * 43
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1", "GET", f"/api/v1/public/shares/{token}/items/0/audio", "1.1", 200),
        None,
    )

    assert logging_config.ShareTokenRedactionFilter().filter(record) is True
    assert token not in record.getMessage()
    assert "/api/v1/public/shares/[redacted]/items/0/audio" in record.getMessage()
