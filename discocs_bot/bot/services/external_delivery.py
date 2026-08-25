"""Delivering audio downloaded from a link into a Telegram chat.

Kept apart from DeliveryService on purpose: that one is built around Navidrome
songs — their ids, covers, and quality profiles. A link has none of those, and
brings its own problems instead (unknown source bitrate, files past Telegram's
upload limit).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from telegram import Bot, InputFile

from bot.config import Settings
from bot.services.external_audio import ExternalAudioError, ExternalTrackInfo, LinkAudioService
from bot.services.media_cache import MediaCache
from bot.services.transcoder import Transcoder
from bot.storage.db import Database
from bot.storage.models import Track, utc_now_iso
from bot.utils.external_quality import (
    estimate_bitrate_kbps,
    part_count,
    part_ranges,
    part_title,
    target_bitrate_kbps,
)
from bot.utils.telegram_retry import telegram_retry

logger = logging.getLogger(__name__)

TELEGRAM_CONNECT_TIMEOUT = 30
TELEGRAM_UPLOAD_TIMEOUT = 300
THUMBNAIL_FILENAME = "cover.jpg"


@dataclass(slots=True)
class PreparedExternalAudio:
    info: ExternalTrackInfo
    parts: list[Path]
    profile: str
    duration: int | None
    thumbnail: Path | None = None
    temp_paths: list[Path] = field(default_factory=list)

    def cleanup(self) -> None:
        for path in self.temp_paths:
            path.unlink(missing_ok=True)
        self.temp_paths.clear()


def track_for(info: ExternalTrackInfo) -> Track:
    """A Track shim so the shared transcoder can write tags."""
    return Track(
        id=info.media_key,
        title=info.title,
        artist=info.artist or info.source,
        album=info.source,
        duration=info.duration,
    )


class ExternalDeliveryService:
    def __init__(
        self,
        settings: Settings,
        links: LinkAudioService,
        transcoder: Transcoder,
        cache: MediaCache,
        db: Database,
    ) -> None:
        self._settings = settings
        self._links = links
        self._transcoder = transcoder
        self._cache = cache
        self._db = db

    async def source_file(self, url: str, info: ExternalTrackInfo) -> Path:
        """The downloaded original, from cache when we already have it."""
        cached = self._cache.find(info.media_key)
        if cached is not None:
            logger.info("Media cache hit media_key=%s path=%s", info.media_key, cached.name)
            return cached
        root = self._cache.ensure_root()
        path = await self._links.download(url, info, root)
        self._cache.trim()
        return path

    async def prepare_mp3(
        self,
        info: ExternalTrackInfo,
        source_path: Path,
        work_dir: Path,
    ) -> PreparedExternalAudio:
        work_dir.mkdir(parents=True, exist_ok=True)
        duration = await self._transcoder.get_audio_duration(source_path)
        temp_paths: list[Path] = []
        thumbnail = await self._thumbnail(info, work_dir, temp_paths)

        audio_path, profile = await self._mp3_source(
            info, source_path, work_dir, duration, temp_paths
        )
        parts = await self._split_if_needed(audio_path, duration, work_dir, temp_paths)
        return PreparedExternalAudio(
            info=info,
            parts=parts,
            profile=profile,
            duration=duration,
            thumbnail=thumbnail,
            temp_paths=temp_paths,
        )

    async def _mp3_source(
        self,
        info: ExternalTrackInfo,
        source_path: Path,
        work_dir: Path,
        duration: int | None,
        temp_paths: list[Path],
    ) -> tuple[Path, str]:
        source_kbps = await self._transcoder.get_audio_bitrate_kbps(source_path)
        if source_kbps is None:
            size = source_path.stat().st_size if source_path.exists() else None
            source_kbps = estimate_bitrate_kbps(size, duration)

        if source_path.suffix.lower() == ".mp3":
            # Already mp3: re-encoding at the same bitrate would only lose quality.
            return source_path, f"mp3:{source_kbps or 'source'}"

        bitrate = target_bitrate_kbps(
            source_kbps,
            headroom=self._settings.external_bitrate_headroom,
            max_kbps=self._settings.external_max_bitrate_kbps,
        )
        output = work_dir / f"{info.media_key}.mp3"
        await self._transcoder.transcode_to_mp3(
            source_path,
            output,
            track=track_for(info),
            cover_path=None,
            bitrate=f"{bitrate}k",
        )
        temp_paths.append(output)
        return output, f"mp3:{bitrate}"

    async def _split_if_needed(
        self,
        audio_path: Path,
        duration: int | None,
        work_dir: Path,
        temp_paths: list[Path],
    ) -> list[Path]:
        limit = self._settings.max_telegram_audio_bytes
        size = audio_path.stat().st_size
        if size <= limit:
            return [audio_path]

        if not duration:
            raise ExternalAudioError(
                "Cannot split audio of unknown duration",
                user_message="Файл слишком большой, а длительность не определяется — не могу порезать.",
            )
        count = part_count(size, limit)
        if count > self._settings.external_max_parts:
            raise ExternalAudioError(
                f"Would need {count} parts",
                user_message=(
                    "Слишком длинное: не помещается даже в "
                    f"{self._settings.external_max_parts} части по "
                    f"{self._settings.max_telegram_audio_mb} МБ."
                ),
            )

        parts: list[Path] = []
        for index, (start, length) in enumerate(part_ranges(float(duration), count)):
            part_path = work_dir / f"{audio_path.stem}.part{index + 1}.mp3"
            await self._transcoder.split_audio(
                audio_path,
                part_path,
                start_seconds=start,
                duration_seconds=length,
            )
            temp_paths.append(part_path)
            parts.append(part_path)
        logger.info("Split %s into %s parts (%s bytes)", audio_path.name, count, size)
        return parts

    async def _thumbnail(
        self,
        info: ExternalTrackInfo,
        work_dir: Path,
        temp_paths: list[Path],
    ) -> Path | None:
        source = self._links.thumbnail_path(self._cache.root, info.media_key)
        if source is None:
            return None
        output = work_dir / f"{info.media_key}.thumb.jpg"
        thumbnail = await self._transcoder.make_telegram_thumbnail(source, output)
        if thumbnail is not None:
            temp_paths.append(thumbnail)
        return thumbnail

    # -- sending -----------------------------------------------------------

    async def send_cached(self, bot: Bot, *, chat_id: int, parts: list) -> bool:
        """Resend a previous delivery by file_id. False when nothing is cached."""
        if not parts:
            return False
        for row in parts:
            await telegram_retry(
                lambda file_id=row["telegram_file_id"]: bot.send_audio(
                    chat_id=chat_id,
                    audio=file_id,
                    connect_timeout=TELEGRAM_CONNECT_TIMEOUT,
                    read_timeout=TELEGRAM_UPLOAD_TIMEOUT,
                    write_timeout=TELEGRAM_UPLOAD_TIMEOUT,
                ),
                description="send_audio_cached",
            )
        return True

    async def send_prepared(
        self,
        bot: Bot,
        *,
        chat_id: int,
        prepared: PreparedExternalAudio,
    ) -> list[dict]:
        info = prepared.info
        count = len(prepared.parts)
        sent: list[dict] = []
        for index, path in enumerate(prepared.parts):
            duration = await self._transcoder.get_audio_duration(path)
            kwargs: dict = {
                "chat_id": chat_id,
                "title": part_title(info.title, index, count),
                "performer": info.artist or info.source,
                "connect_timeout": TELEGRAM_CONNECT_TIMEOUT,
                "read_timeout": TELEGRAM_UPLOAD_TIMEOUT,
                "write_timeout": TELEGRAM_UPLOAD_TIMEOUT,
            }
            if duration:
                kwargs["duration"] = duration
            thumb_file = None
            if prepared.thumbnail and prepared.thumbnail.exists():
                thumb_file = prepared.thumbnail.open("rb")
                kwargs["thumbnail"] = InputFile(thumb_file, filename=THUMBNAIL_FILENAME)
            try:
                with path.open("rb") as audio_file:
                    kwargs["audio"] = InputFile(
                        audio_file,
                        filename=f"{path.stem}{path.suffix}",
                    )
                    message = await telegram_retry(
                        lambda: bot.send_audio(**kwargs),
                        description="send_audio_external",
                    )
            finally:
                if thumb_file:
                    thumb_file.close()
            if message.audio:
                sent.append(
                    {
                        "file_id": message.audio.file_id,
                        "file_size": message.audio.file_size,
                        "duration": message.audio.duration,
                    }
                )
        return sent

    async def remember_delivery(
        self,
        info: ExternalTrackInfo,
        *,
        profile: str,
        parts: list[dict],
    ) -> None:
        if not parts:
            return
        await self._db.save_external_parts(
            media_key=info.media_key,
            profile=profile,
            parts=parts,
            now=utc_now_iso(),
        )
