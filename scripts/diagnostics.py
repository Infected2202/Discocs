from __future__ import annotations

import os
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass(frozen=True)
class CapturedStderr:
    text: str

    @property
    def has_warning(self) -> bool:
        return "[ WARNING" in self.text

    @property
    def has_info(self) -> bool:
        return "[   INFO" in self.text


@contextmanager
def captured_stderr(enabled: bool = True):
    if not enabled:
        yield CapturedStderr("")
        return

    sys.stderr.flush()
    original_fd = os.dup(2)
    capture = CapturedStderr("")
    with tempfile.TemporaryFile(mode="w+b") as captured:
        os.dup2(captured.fileno(), 2)
        try:
            yield capture
        finally:
            sys.stderr.flush()
            os.dup2(original_fd, 2)
            os.close(original_fd)
            captured.seek(0)
            object.__setattr__(
                capture,
                "text",
                captured.read().decode(errors="replace").strip(),
            )


def timed_call(func) -> tuple[object, float]:
    started = time.perf_counter()
    result = func()
    return result, time.perf_counter() - started
