from pathlib import Path
from unittest.mock import Mock, patch
import subprocess

import numpy as np
import pytest

from app.embedder import FfmpegDecodeError, load_audio_with_ffmpeg


def test_load_audio_with_ffmpeg_reads_float32_stdout(tmp_path: Path):
    payload = np.array([0.1, -0.2, 0.3], dtype=np.float32).tobytes()
    path = tmp_path / "track.opus"
    path.write_bytes(b"fake")

    with patch("app.embedder.subprocess.run") as run:
        run.return_value = Mock(stdout=payload)

        audio = load_audio_with_ffmpeg(path)

    assert np.allclose(audio, np.array([0.1, -0.2, 0.3], dtype=np.float32))
    command = run.call_args.args[0]
    assert command[:4] == ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    assert "16000" in command


def test_load_audio_with_ffmpeg_accepts_thread_override(tmp_path: Path, monkeypatch):
    payload = np.array([0.1], dtype=np.float32).tobytes()
    path = tmp_path / "track.mp3"
    path.write_bytes(b"fake")
    monkeypatch.setenv("DISCOCS_FFMPEG_THREADS", "1")

    with patch("app.embedder.subprocess.run") as run:
        run.return_value = Mock(stdout=payload)

        audio = load_audio_with_ffmpeg(path)

    assert np.allclose(audio, np.array([0.1], dtype=np.float32))
    command = run.call_args.args[0]
    assert command[:6] == ["ffmpeg", "-hide_banner", "-loglevel", "error", "-threads", "1"]


def test_load_audio_with_ffmpeg_includes_ffmpeg_stderr(tmp_path: Path):
    path = tmp_path / "broken.mp3"
    path.write_bytes(b"not audio")
    error = subprocess.CalledProcessError(
        returncode=254,
        cmd=["ffmpeg"],
        stderr=b"Invalid data found when processing input\n",
    )

    with patch("app.embedder.subprocess.run", side_effect=error):
        with pytest.raises(FfmpegDecodeError) as exc_info:
            load_audio_with_ffmpeg(path)

    assert str(path) in str(exc_info.value)
    assert "exit code 254" in str(exc_info.value)
    assert "Invalid data found when processing input" in str(exc_info.value)


def test_load_audio_with_ffmpeg_reports_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="Audio file not found"):
        load_audio_with_ffmpeg(tmp_path / "missing.mp3")
