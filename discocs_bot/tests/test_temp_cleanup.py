"""Sweeping work files a crashed delivery could not clean up itself."""
from __future__ import annotations

import os
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.pop("bot", None)

from bot.utils.temp_cleanup import sweep_stale_files

HOUR = 3600.0


def aged(path: Path, hours: float) -> Path:
    path.write_bytes(b"x")
    stamp = time.time() - hours * HOUR
    os.utime(path, (stamp, stamp))
    return path


def test_old_files_are_removed_and_fresh_ones_kept(tmp_path: Path):
    stale = aged(tmp_path / "old.mp3", hours=12)
    fresh = aged(tmp_path / "new.mp3", hours=1)

    removed = sweep_stale_files(tmp_path, max_age_seconds=6 * HOUR)

    assert removed == [stale]
    assert not stale.exists()
    assert fresh.exists()


def test_subdirectories_are_left_alone(tmp_path: Path):
    nested = tmp_path / "external"
    nested.mkdir()
    stamp = time.time() - 48 * HOUR
    os.utime(nested, (stamp, stamp))

    assert sweep_stale_files(tmp_path, max_age_seconds=6 * HOUR) == []
    assert nested.exists()


def test_missing_directory_is_not_an_error(tmp_path: Path):
    assert sweep_stale_files(tmp_path / "nope", max_age_seconds=HOUR) == []


def test_cutoff_is_taken_from_the_supplied_clock(tmp_path: Path):
    path = aged(tmp_path / "file.mp3", hours=2)

    assert sweep_stale_files(tmp_path, max_age_seconds=HOUR, now=time.time() - 10 * HOUR) == []
    assert path.exists()
