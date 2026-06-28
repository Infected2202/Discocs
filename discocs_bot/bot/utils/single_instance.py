from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_ALREADY_EXISTS = 183

_mutex_handle: int | None = None
_lock_file = None


def _is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    handle = ctypes.windll.kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def _mutex_name(data_dir: Path) -> str:
    project_root = data_dir.resolve().parent
    digest = hashlib.sha256(str(project_root).lower().encode("utf-8")).hexdigest()[:16]
    return f"Local\\discocs_bot_{digest}"


def pid_file_path(data_dir: Path) -> Path:
    return data_dir / "bot.pid"


def lock_file_path(data_dir: Path) -> Path:
    return data_dir / "bot.lock"


def _acquire_posix_lock(data_dir: Path) -> None:
    global _lock_file

    import fcntl

    data_dir.mkdir(parents=True, exist_ok=True)
    path = lock_file_path(data_dir)
    lock_file = path.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        logger.error("Bot already running in this project. Stop the existing process first.")
        raise SystemExit(1)
    _lock_file = lock_file


def _release_posix_lock() -> None:
    global _lock_file

    if _lock_file is None:
        return

    import fcntl

    fcntl.flock(_lock_file.fileno(), fcntl.LOCK_UN)
    _lock_file.close()
    _lock_file = None


def acquire(data_dir: Path) -> None:
    global _mutex_handle

    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        mutex_name = _mutex_name(data_dir)
        handle = kernel32.CreateMutexW(None, True, mutex_name)
        if not handle:
            logger.error("Failed to acquire bot instance lock")
            raise SystemExit(1)
        if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            logger.error("Bot already running in this project. Run stop.bat first.")
            raise SystemExit(1)
        _mutex_handle = handle
    else:
        _acquire_posix_lock(data_dir)

    data_dir.mkdir(parents=True, exist_ok=True)
    path = pid_file_path(data_dir)

    if path.exists():
        try:
            old_pid = int(path.read_text(encoding="utf-8").strip())
        except ValueError:
            old_pid = 0
        if old_pid and old_pid != os.getpid() and _is_process_running(old_pid):
            if sys.platform == "win32":
                ctypes.windll.kernel32.CloseHandle(_mutex_handle)
                _mutex_handle = None
            else:
                _release_posix_lock()
            logger.error(
                "Bot already running (PID %s). Stop it before starting another instance.",
                old_pid,
            )
            raise SystemExit(1)
        path.unlink(missing_ok=True)

    path.write_text(str(os.getpid()), encoding="utf-8")


def release(data_dir: Path) -> None:
    global _mutex_handle

    path = pid_file_path(data_dir)
    if path.exists():
        try:
            stored_pid = int(path.read_text(encoding="utf-8").strip())
        except ValueError:
            stored_pid = 0
        if stored_pid == os.getpid():
            path.unlink(missing_ok=True)

    if _mutex_handle:
        ctypes.windll.kernel32.CloseHandle(_mutex_handle)
        _mutex_handle = None

    if sys.platform != "win32":
        _release_posix_lock()
