from __future__ import annotations

import os
from pathlib import Path

from bot.utils import single_instance


def test_active_pid_from_file_cleans_stale_pid(monkeypatch, tmp_path: Path):
    pid_path = tmp_path / "bot.pid"
    pid_path.write_text("999999", encoding="utf-8")
    monkeypatch.setattr(single_instance, "_is_process_running", lambda _pid: False)

    active_pid = single_instance._active_pid_from_file(pid_path)

    assert active_pid is None
    assert not pid_path.exists()


def test_acquire_writes_current_pid_after_stale_pid_cleanup(monkeypatch, tmp_path: Path):
    pid_path = single_instance.pid_file_path(tmp_path)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("999999", encoding="utf-8")

    monkeypatch.setattr(single_instance, "_acquire_process_lock", lambda _path: None)
    monkeypatch.setattr(single_instance, "_release_process_lock", lambda: None)
    monkeypatch.setattr(single_instance, "_is_process_running", lambda _pid: False)

    single_instance.acquire(tmp_path)

    assert pid_path.read_text(encoding="utf-8") == str(os.getpid())
