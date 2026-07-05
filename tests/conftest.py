from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time

import pytest


os.environ.setdefault(
    "DISCOCS_LOG_DIR",
    str(Path(tempfile.gettempdir()) / "discocs-test-logs" / str(os.getpid())),
)


def pytest_configure(config) -> None:
    """Give each pytest session its own unique basetemp directory.

    On Windows, SQLite WAL mode keeps .db files locked briefly after a test
    ends. If pytest reuses the same basetemp across sessions, it tries to
    delete the previous session's numbered dirs and hits WinError 32.
    Unique-per-session basetemp eliminates cross-session cleanup entirely.

    Under pytest-xdist each worker runs its own session (and its own call to
    this hook) — worker id is folded into the path so two workers starting
    in the same second don't race on the same numbered subdirs.
    """
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "master")
    base = Path(tempfile.gettempdir()) / "pytest-discocs" / f"{int(time.time())}-{worker_id}"
    base.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = base


@pytest.fixture(autouse=True)
def _stop_maintenance_loop_after_test():
    """Stop the maintenance loop after each test.

    TestClient triggers @app.on_event("startup") which starts a daemon thread
    that holds a SQLite connection (WAL mode). Without explicit shutdown the
    thread outlives the test and keeps app.db locked, preventing pytest from
    cleaning up within-session numbered dirs on Windows.
    """
    yield
    import threading
    import app.state as _state
    _state.MAINTENANCE_STOP.set()
    for thread in threading.enumerate():
        if thread.name == "discocs-maintenance":
            thread.join(timeout=3.0)
    _state.MAINTENANCE_STOP.clear()
