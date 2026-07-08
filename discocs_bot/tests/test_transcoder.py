from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from bot.services.transcoder import Transcoder
from bot.storage.models import Track


def _track() -> Track:
    return Track(id="song-1", title="Track", artist="Artist", album="Album", year=2024)


def test_build_transcode_command_copies_plain_mp3_without_cover(tmp_path: Path):
    transcoder = Transcoder(SimpleNamespace(transcode_workers=1, transcode_fast=False))

    cmd = transcoder._build_transcode_command(
        tmp_path / "input.mp3",
        tmp_path / "output.mp3",
        track=_track(),
        cover_path=None,
        audio_format="mp3",
        bitrate="320k",
    )

    assert "-c:a" in cmd
    assert "copy" in cmd
    assert "libmp3lame" not in cmd


def test_build_transcode_command_embeds_cover_for_flac(tmp_path: Path):
    cover_path = tmp_path / "cover.jpg"
    cover_path.write_bytes(b"cover")
    transcoder = Transcoder(SimpleNamespace(transcode_workers=1, transcode_fast=True))

    cmd = transcoder._build_transcode_command(
        tmp_path / "input.wav",
        tmp_path / "output.flac",
        track=_track(),
        cover_path=cover_path,
        audio_format="flac",
        bitrate="320k",
    )

    assert cmd.count("-map") == 2
    assert "mjpeg" in cmd
    assert "attached_pic" in cmd
    assert "copy" not in cmd
