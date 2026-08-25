"""Preparing and sending audio that came from a link."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.pop("bot", None)

from bot.services.external_audio import ExternalAudioError, ExternalTrackInfo
from bot.services.external_delivery import ExternalDeliveryService, track_for
from bot.services.media_cache import MediaCache
from bot.storage.db import Database

MB = 1024 * 1024


def info() -> ExternalTrackInfo:
    return ExternalTrackInfo(
        media_key="abc123",
        url_key="youtube:abc123",
        source="youtube",
        webpage_url="https://youtu.be/abc123",
        title="Xtal",
        artist="Aphex Twin",
        duration=300,
    )


def settings(**overrides) -> SimpleNamespace:
    base = {
        "max_telegram_audio_mb": 50,
        "max_telegram_audio_bytes": 50 * MB,
        "external_max_parts": 4,
        "external_bitrate_headroom": 1.0,
        "external_max_bitrate_kbps": 320,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeTranscoder:
    def __init__(self, *, duration: int | None = 300, bitrate: int | None = 128) -> None:
        self.duration = duration
        self.bitrate = bitrate
        self.transcodes: list[tuple[Path, Path, str]] = []
        self.splits: list[tuple[Path, Path, float, float]] = []

    async def get_audio_duration(self, path: Path) -> int | None:
        return self.duration

    async def get_audio_bitrate_kbps(self, path: Path) -> int | None:
        return self.bitrate

    async def transcode_to_mp3(self, input_path, output_path, *, track, cover_path, bitrate):
        self.transcodes.append((input_path, output_path, bitrate))
        output_path.write_bytes(b"x" * MB)

    async def split_audio(self, input_path, output_path, *, start_seconds, duration_seconds):
        self.splits.append((input_path, output_path, start_seconds, duration_seconds))
        output_path.write_bytes(b"x" * 1024)
        return output_path

    async def make_telegram_thumbnail(self, cover_path: Path, output_path: Path) -> Path | None:
        output_path.write_bytes(b"jpeg")
        return output_path


def service(tmp_path: Path, transcoder: FakeTranscoder, **overrides) -> ExternalDeliveryService:
    return ExternalDeliveryService(
        settings(**overrides),
        SimpleNamespace(thumbnail_path=lambda root, key: None),
        transcoder,
        MediaCache(tmp_path / "cache", max_bytes=10 * MB),
        db=None,
    )


def test_track_shim_carries_metadata_into_ffmpeg_tags():
    track = track_for(info())

    assert track.title == "Xtal"
    assert track.artist == "Aphex Twin"
    assert track.album == "youtube"


def test_mp3_source_is_reused_without_re_encoding(tmp_path: Path):
    transcoder = FakeTranscoder()
    source = tmp_path / "abc123.mp3"
    source.write_bytes(b"x" * MB)

    prepared = asyncio.run(
        service(tmp_path, transcoder).prepare_mp3(info(), source, tmp_path / "work")
    )

    assert prepared.parts == [source]
    assert transcoder.transcodes == []


def test_opus_source_is_transcoded_at_the_source_bitrate(tmp_path: Path):
    transcoder = FakeTranscoder(bitrate=128)
    source = tmp_path / "abc123.opus"
    source.write_bytes(b"x" * MB)

    prepared = asyncio.run(
        service(tmp_path, transcoder).prepare_mp3(info(), source, tmp_path / "work")
    )

    assert len(transcoder.transcodes) == 1
    assert transcoder.transcodes[0][2] == "128k"
    assert prepared.profile == "mp3:128"
    assert prepared.parts[0].suffix == ".mp3"


def test_headroom_setting_raises_the_encode_bitrate(tmp_path: Path):
    transcoder = FakeTranscoder(bitrate=128)
    source = tmp_path / "abc123.opus"
    source.write_bytes(b"x" * MB)

    asyncio.run(
        service(tmp_path, transcoder, external_bitrate_headroom=1.5).prepare_mp3(
            info(), source, tmp_path / "work"
        )
    )

    assert transcoder.transcodes[0][2] == "192k"


def test_oversized_audio_is_split_into_parts(tmp_path: Path):
    # Shrinking the upload limit is the same arithmetic as a 140 MB file
    # against Telegram's 50 MB, without writing 140 MB to disk.
    transcoder = FakeTranscoder(duration=3600)
    source = tmp_path / "abc123.mp3"
    source.write_bytes(b"x" * MB)

    prepared = asyncio.run(
        service(tmp_path, transcoder, max_telegram_audio_bytes=400 * 1024).prepare_mp3(
            info(), source, tmp_path / "work"
        )
    )

    assert len(prepared.parts) == 3
    assert len(transcoder.splits) == 3
    assert transcoder.splits[0][2] == 0.0
    assert transcoder.splits[-1][2] == pytest.approx(2400.0)
    assert transcoder.splits[-1][3] == pytest.approx(1200.0)


def test_audio_needing_too_many_parts_is_refused(tmp_path: Path):
    transcoder = FakeTranscoder(duration=3600)
    source = tmp_path / "abc123.mp3"
    source.write_bytes(b"x" * MB)

    with pytest.raises(ExternalAudioError):
        asyncio.run(
            service(tmp_path, transcoder, max_telegram_audio_bytes=100 * 1024).prepare_mp3(
                info(), source, tmp_path / "work"
            )
        )


def test_split_is_refused_when_duration_is_unknown(tmp_path: Path):
    transcoder = FakeTranscoder(duration=None)
    source = tmp_path / "abc123.mp3"
    source.write_bytes(b"x" * MB)

    with pytest.raises(ExternalAudioError):
        asyncio.run(
            service(tmp_path, transcoder, max_telegram_audio_bytes=400 * 1024).prepare_mp3(
                info(), source, tmp_path / "work"
            )
        )


def test_prepared_cleanup_removes_only_generated_files(tmp_path: Path):
    transcoder = FakeTranscoder(bitrate=128)
    source = tmp_path / "abc123.opus"
    source.write_bytes(b"x" * MB)

    prepared = asyncio.run(
        service(tmp_path, transcoder).prepare_mp3(info(), source, tmp_path / "work")
    )
    generated = prepared.parts[0]
    prepared.cleanup()

    assert not generated.exists()
    assert source.exists()


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def test_external_media_and_parts_round_trip(tmp_path: Path):
    db = Database(SimpleNamespace(sqlite_path=tmp_path / "bot.sqlite"))

    async def scenario():
        await db.connect()
        try:
            await db.save_external_media(
                media_key="abc123",
                url_key="youtube:abc123",
                source="youtube",
                webpage_url="https://youtu.be/abc123",
                title="Xtal",
                artist="Aphex Twin",
                duration=300,
                thumbnail_url=None,
                now="2026-08-26T00:00:00+00:00",
            )
            row = await db.get_external_media("abc123")
            assert row["title"] == "Xtal"
            assert await db.get_external_parts("abc123") == []

            await db.save_external_parts(
                media_key="abc123",
                profile="mp3:128",
                parts=[
                    {"file_id": "f1", "file_size": 10, "duration": 150},
                    {"file_id": "f2", "file_size": 11, "duration": 150},
                ],
                now="2026-08-26T00:00:00+00:00",
            )
            parts = await db.get_external_parts("abc123")
            assert [part["telegram_file_id"] for part in parts] == ["f1", "f2"]
            assert {part["part_count"] for part in parts} == {2}

            # A re-delivery replaces the set instead of mixing profiles.
            await db.save_external_parts(
                media_key="abc123",
                profile="mp3:320",
                parts=[{"file_id": "single", "file_size": 12, "duration": 300}],
                now="2026-08-26T01:00:00+00:00",
            )
            parts = await db.get_external_parts("abc123")
            assert [part["telegram_file_id"] for part in parts] == ["single"]

            await db.delete_external_parts("abc123")
            assert await db.get_external_parts("abc123") == []
        finally:
            await db.close()

    asyncio.run(scenario())
