import asyncio
import logging
import re
from contextlib import ExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from telegram import Bot, InputFile, InputMediaAudio, InputMediaDocument, Message
from telegram.error import BadRequest, NetworkError, TimedOut

from bot.config import Settings
from bot.services.navidrome import NavidromeClient, NavidromeError
from bot.services.transcoder import TranscodeError, Transcoder
from bot.storage.db import Database
from bot.storage.models import Album, Track, utc_now_iso
from bot.storage.user_prefs import UserDeliveryPrefs, delivery_prefs_from_profile
from bot.utils.audio_quality import (
    normalize_suffix,
    parse_bitrate_kbps,
    send_as_document,
    should_send_original,
    should_use_navidrome_transcode,
    source_cache_profile,
)
from bot.utils.caption import format_album_header
from bot.utils.prep_timing import PrepTimer
from bot.utils.telegram_retry import telegram_retry

logger = logging.getLogger(__name__)

MEDIA_GROUP_LIMIT = 10
TELEGRAM_CONNECT_TIMEOUT = 30
TELEGRAM_UPLOAD_TIMEOUT = 300
COVER_FILENAME = "cover.jpg"
NO_AUDIO_SOURCE_PREPARED = "No audio source prepared"
# Telegram's `thumbnail` param (audio player artwork, unlike a regular `photo`)
# must be a JPEG no larger than 320x320 and 200 KB, or it's silently dropped.
THUMBNAIL_COVER_SIZE = 320


@dataclass(slots=True)
class _ResolvedDelivery:
    use_original: bool
    cache_bitrate: str
    file_extension: str
    as_document: bool


@dataclass(slots=True)
class PreparedAudio:
    track: Track
    file_id: str | None = None
    file_path: Path | None = None
    duration: int | None = None
    filename: str = ""
    cache_bitrate: str = ""
    as_document: bool = False
    temp_paths: list[Path] = field(default_factory=list)

    def cleanup(self) -> None:
        for path in self.temp_paths:
            if path.exists():
                path.unlink(missing_ok=True)
        self.temp_paths.clear()


def _telegram_audio_metadata(prepared: PreparedAudio) -> dict:
    """Telegram often ignores artist/title tags in OGG; pass them via API fields."""
    fields: dict = {
        "title": prepared.track.title,
        "performer": prepared.track.artist,
    }
    if prepared.duration:
        fields["duration"] = prepared.duration
    return fields


@dataclass(slots=True)
class _PreparedSource:
    source_path: Path
    out_path: Path
    temp_paths: list[Path]
    source_bitrate: int | None
    use_server_mp3: bool


class DeliveryService:
    def __init__(
        self,
        settings: Settings,
        navidrome: NavidromeClient,
        transcoder: Transcoder,
        db: Database,
    ):
        self._settings = settings
        self._navidrome = navidrome
        self._transcoder = transcoder
        self._db = db

    async def deliver_track(self, bot: Bot, chat_id: int, song_id: str, *, user_id: int | None) -> None:
        timer = PrepTimer(label=f"track:{song_id}")
        async with timer.step("get_song"):
            track = await self._navidrome.get_song(song_id)
        timer.add_detail("format", track.suffix or "?")

        async with timer.step("load_cover"):
            cover_path = await self._load_cover(track, song_id)

        prepared = await self._prepare_audio(
            track,
            user_id=user_id,
            timer=timer,
            cover_path=cover_path,
        )
        try:
            async with timer.step("telegram_upload"):
                await self._send_single_audio(
                    bot, chat_id=chat_id, prepared=prepared, thumb_path=cover_path
                )
            await self._finalize_delivery(timer, user_id=user_id, song_id=song_id)
        finally:
            prepared.cleanup()
            self._cleanup(cover_path)

    async def deliver_track_to_message(
        self,
        bot: Bot,
        *,
        chat_id: int,
        message_id: int,
        song_id: str,
        user_id: int | None,
    ) -> None:
        timer = PrepTimer(label=f"track:{song_id}")
        async with timer.step("get_song"):
            track = await self._navidrome.get_song(song_id)
        timer.add_detail("format", track.suffix or "?")

        async with timer.step("load_cover"):
            cover_path = await self._load_cover(track, song_id)

        prepared = await self._prepare_audio(
            track,
            user_id=user_id,
            timer=timer,
            cover_path=cover_path,
        )
        try:
            async with timer.step("telegram_upload"):
                await self._replace_message_with_audio(
                    bot,
                    chat_id=chat_id,
                    message_id=message_id,
                    prepared=prepared,
                    thumb_path=cover_path,
                )
            await self._finalize_delivery(timer, user_id=user_id, song_id=song_id)
        finally:
            prepared.cleanup()
            self._cleanup(cover_path)

    async def deliver_track_below(
        self,
        bot: Bot,
        chat_id: int,
        song_id: str,
        *,
        user_id: int | None,
    ) -> None:
        await self.deliver_track(bot, chat_id, song_id, user_id=user_id)

    async def _finalize_delivery(
        self,
        timer: PrepTimer,
        *,
        user_id: int | None,
        song_id: str,
    ) -> None:
        await self._log_prep_timing(timer, user_id=user_id, song_id=song_id)
        await self._db.log_event(
            user_id=user_id,
            song_id=song_id,
            event_type="send",
            context=None,
            created_at=utc_now_iso(),
        )

    async def deliver_album(
        self,
        bot: Bot,
        chat_id: int,
        album_id: str,
        *,
        user_id: int | None,
        status_message: Message | None = None,
    ) -> None:
        album, tracks = await self._navidrome.get_album(album_id)
        if not tracks:
            raise NavidromeError("Album has no tracks")

        cover_path = await self._load_cover_for_album(album, album_id)

        update_status = self._album_status_updater(status_message)
        workers = max(1, self._settings.transcode_workers)
        semaphore = asyncio.Semaphore(workers)
        errors: list[Exception] = []
        prepared_count = 0
        progress_lock = asyncio.Lock()

        await update_status(f"Готовлю альбом... 0/{len(tracks)}")

        async def prepare_one(track: Track) -> PreparedAudio | None:
            nonlocal prepared_count
            track_timer = PrepTimer(label=f"album:{album_id}:{track.id}")
            try:
                async with semaphore:
                    prepared = await self._prepare_audio(
                        track,
                        user_id=user_id,
                        timer=track_timer,
                        cover_path=cover_path,
                    )
                await self._log_prep_timing(track_timer, user_id=user_id, song_id=track.id)
                async with progress_lock:
                    prepared_count += 1
                    await update_status(f"Готовлю альбом... {prepared_count}/{len(tracks)}")
                return prepared
            except Exception as exc:
                errors.append(exc)
                logger.exception("Failed to prepare track %s from album %s", track.id, album_id)
                return None

        prepared_items = [
            item
            for item in await asyncio.gather(*(prepare_one(track) for track in tracks))
            if item is not None
        ]

        if not prepared_items and errors:
            self._cleanup(cover_path)
            raise errors[0]

        await update_status("Отправляю обложку альбома...")
        await self._send_header(
            bot,
            chat_id=chat_id,
            cover_path=cover_path,
            caption=format_album_header(album),
        )

        sent_count = 0
        try:
            for item in prepared_items:
                await update_status(f"Отправляю альбом... {sent_count}/{len(tracks)}")
                try:
                    await self._send_single_audio(
                        bot,
                        chat_id=chat_id,
                        prepared=item,
                        thumb_path=cover_path,
                    )
                    sent_count += 1
                    logger.info("Album %s: sent %s/%s", album_id, sent_count, len(tracks))
                except (NetworkError, TimedOut):
                    logger.exception("send_audio failed for album track %s", item.track.id)
            await update_status(f"Отправляю альбом... {sent_count}/{len(tracks)}")
        finally:
            for item in prepared_items:
                item.cleanup()

        if sent_count == 0 and errors:
            self._cleanup(cover_path)
            raise errors[0]

        await self._db.log_event(
            user_id=user_id,
            song_id=album_id,
            event_type="send_album",
            context=f"{sent_count}/{len(tracks)}",
            created_at=utc_now_iso(),
        )
        self._cleanup(cover_path)

    @staticmethod
    def _album_status_updater(status_message: Message | None):
        async def update_status(text: str) -> None:
            if not status_message:
                return
            try:
                await telegram_retry(
                    lambda: status_message.edit_text(text),
                    description="update_album_status",
                )
            except BadRequest:
                logger.debug("Could not update album status message")
            except (NetworkError, TimedOut):
                logger.debug("Could not update album status message after retries")

        return update_status

    async def _send_album_items(
        self,
        bot: Bot,
        *,
        chat_id: int,
        album_id: str,
        prepared_items: list[PreparedAudio],
        cover_path: Path | None,
        total_count: int,
        update_status,
    ) -> int:
        sent_count = 0
        for item in prepared_items:
            await update_status(f"РћС‚РїСЂР°РІР»СЏСЋ Р°Р»СЊР±РѕРј... {sent_count}/{total_count}")
            try:
                await self._send_single_audio(
                    bot,
                    chat_id=chat_id,
                    prepared=item,
                    thumb_path=cover_path,
                )
                sent_count += 1
                logger.info("Album %s: sent %s/%s", album_id, sent_count, total_count)
            except (NetworkError, TimedOut):
                logger.exception("send_audio failed for album track %s", item.track.id)
        await update_status(f"РћС‚РїСЂР°РІР»СЏСЋ Р°Р»СЊР±РѕРј... {sent_count}/{total_count}")
        return sent_count

    async def _prepare_audio(
        self,
        track: Track,
        *,
        user_id: int | None,
        use_cache: bool = True,
        timer: PrepTimer | None = None,
        cover_path: Path | None = None,
    ) -> PreparedAudio:
        temp_dir = self._settings.temp_dir
        temp_dir.mkdir(parents=True, exist_ok=True)
        now = utc_now_iso()
        prefs = await self._user_delivery_prefs(user_id)
        has_cover = bool(cover_path and cover_path.exists())
        source_bitrate = track.bitrate_kbps

        if use_cache:
            cached_audio = await self._resolve_cached_audio(
                track,
                prefs,
                source_bitrate=source_bitrate,
                has_cover=has_cover,
                user_id=user_id,
                now=now,
                timer=timer,
            )
            if cached_audio is not None:
                return cached_audio

        await self._record_cache_miss(track, prefs, user_id=user_id, now=now)
        if timer:
            timer.add_detail("cache", "miss")

        prepared_source = await self._download_prepared_source(
            track,
            prefs,
            source_bitrate=source_bitrate,
            temp_dir=temp_dir,
            timer=timer,
        )

        async with self._timed_step(timer, "transcode"):
            send_path, delivery = await self._prepare_audio_file(
                prepared_source.source_path,
                prepared_source.out_path,
                track,
                prefs=prefs,
                source_suffix="mp3" if prepared_source.use_server_mp3 else track.suffix,
                source_bitrate_kbps=prepared_source.source_bitrate,
                cover_path=cover_path,
            )
        if timer:
            if delivery.use_original:
                timer.add_detail("delivery", "original")
            else:
                timer.add_detail("delivery", "transcoded")
        if timer and send_path.exists():
            timer.add_detail("output_mb", round(send_path.stat().st_size / (1024 * 1024), 2))

        file_size = Transcoder.get_file_size(send_path)
        if file_size > self._settings.max_telegram_audio_bytes:
            raise TranscodeError("file_too_large")

        duration = track.duration
        if duration is None:
            async with self._timed_step(timer, "ffprobe"):
                duration = await self._transcoder.get_audio_duration(send_path)
        elif timer:
            timer.add_detail("ffprobe", "skipped")

        if send_path not in prepared_source.temp_paths:
            prepared_source.temp_paths.append(send_path)

        return PreparedAudio(
            track=track,
            file_path=send_path,
            duration=track.duration or duration,
            filename=self._audio_filename(track, extension=delivery.file_extension),
            cache_bitrate=delivery.cache_bitrate,
            as_document=delivery.as_document,
            temp_paths=prepared_source.temp_paths,
        )

    async def _resolve_cached_audio(
        self,
        track: Track,
        prefs: UserDeliveryPrefs,
        *,
        source_bitrate: int | None,
        has_cover: bool,
        user_id: int | None,
        now: str,
        timer: PrepTimer | None,
    ) -> PreparedAudio | None:
        async with self._timed_step(timer, "cache_lookup"):
            cached = await self._lookup_cached(
                track,
                prefs,
                source_bitrate_kbps=source_bitrate,
                has_cover=has_cover,
            )
        if cached is None:
            return None

        file_id, cache_key, as_document, extension = cached
        await self._db.touch_cache(track.id, cache_key, now)
        await self._db.log_event(
            user_id=user_id,
            song_id=track.id,
            event_type="cache_hit",
            context=cache_key,
            created_at=now,
        )
        if timer:
            timer.add_detail("cache", "hit")
            timer.add_detail("profile", prefs.profile)
            timer.add_detail("cache_key", cache_key)
        return PreparedAudio(
            track=track,
            file_id=file_id,
            duration=track.duration,
            filename=self._audio_filename(track, extension=extension),
            cache_bitrate=cache_key,
            as_document=as_document,
        )

    async def _record_cache_miss(
        self,
        track: Track,
        prefs: UserDeliveryPrefs,
        *,
        user_id: int | None,
        now: str,
    ) -> None:
        await self._db.log_event(
            user_id=user_id,
            song_id=track.id,
            event_type="cache_miss",
            context=prefs.profile,
            created_at=now,
        )

    async def _download_prepared_source(
        self,
        track: Track,
        prefs: UserDeliveryPrefs,
        *,
        source_bitrate: int | None,
        temp_dir: Path,
        timer: PrepTimer | None,
    ) -> _PreparedSource:
        prepared_source = self._build_prepared_source(
            track,
            prefs,
            source_bitrate=source_bitrate,
            temp_dir=temp_dir,
        )
        max_bit_rate = self._navidrome_stream_bitrate(prefs) if prepared_source.use_server_mp3 else None
        async with self._timed_step(timer, "download"):
            downloaded_bytes = await self._navidrome.download_stream(
                track.id,
                prepared_source.source_path,
                max_bit_rate=max_bit_rate,
            )
        if timer:
            timer.add_detail("download_mb", round(downloaded_bytes / (1024 * 1024), 2))
            timer.add_detail("profile", prefs.profile)
            if prepared_source.use_server_mp3:
                timer.add_detail("download_mode", "navidrome_mp3")
        prepared_source.source_bitrate = await self._resolve_source_bitrate(
            prepared_source.source_path,
            source_bitrate=source_bitrate,
            use_server_mp3=prepared_source.use_server_mp3,
            timer=timer,
        )
        return prepared_source

    def _build_prepared_source(
        self,
        track: Track,
        prefs: UserDeliveryPrefs,
        *,
        source_bitrate: int | None,
        temp_dir: Path,
    ) -> _PreparedSource:
        extension = track.suffix or "audio"
        use_server_mp3 = should_use_navidrome_transcode(
            track.suffix,
            source_bitrate,
            prefs,
            enabled=self._settings.navidrome_stream_max_bitrate > 0,
        )
        source_path = (
            temp_dir / f"{track.id}.source.mp3"
            if use_server_mp3
            else temp_dir / f"{track.id}.source.{extension}"
        )
        out_path = temp_dir / f"{track.id}.out.{prefs.file_extension}"
        return _PreparedSource(
            source_path=source_path,
            out_path=out_path,
            temp_paths=[source_path, out_path],
            source_bitrate=source_bitrate,
            use_server_mp3=use_server_mp3,
        )

    async def _resolve_source_bitrate(
        self,
        source_path: Path,
        *,
        source_bitrate: int | None,
        use_server_mp3: bool,
        timer: PrepTimer | None,
    ) -> int | None:
        resolved_bitrate = source_bitrate
        if resolved_bitrate is None or use_server_mp3:
            async with self._timed_step(timer, "ffprobe_bitrate"):
                probed = await self._transcoder.get_audio_bitrate_kbps(source_path)
            if probed:
                resolved_bitrate = probed
        if timer and resolved_bitrate:
            timer.add_detail("source_kbps", resolved_bitrate)
        return resolved_bitrate

    async def _lookup_cached(
        self,
        track: Track,
        prefs: UserDeliveryPrefs,
        *,
        source_bitrate_kbps: int | None,
        has_cover: bool,
    ) -> tuple[str, str, bool, str] | None:
        keys: list[str] = []
        if should_send_original(track.suffix, source_bitrate_kbps, prefs):
            keys.append(
                source_cache_profile(
                    track.suffix,
                    source_bitrate_kbps,
                    transcoded_with_cover=has_cover,
                )
            )
        keys.append(prefs.cache_profile(with_cover=has_cover))

        seen: set[str] = set()
        for key in keys:
            if key in seen:
                continue
            seen.add(key)
            file_id = await self._db.get_cached_file_id(track.id, bitrate=key)
            if not file_id:
                continue
            logger.info("Cache hit for track %s using key=%s", track.id, key)
            as_document, extension = self._cache_hit_delivery_attrs(
                track,
                prefs,
                cache_key=key,
                has_cover=has_cover,
            )
            return file_id, key, as_document, extension
        return None

    @staticmethod
    def _cache_hit_delivery_attrs(
        track: Track,
        prefs: UserDeliveryPrefs,
        *,
        cache_key: str,
        has_cover: bool,
    ) -> tuple[bool, str]:
        transcoded_keys = {
            prefs.cache_profile(with_cover=has_cover),
            prefs.cache_profile(with_cover=False),
        }
        if cache_key in transcoded_keys:
            return prefs.as_document, prefs.file_extension
        suffix = track.suffix or "mp3"
        return send_as_document(suffix), normalize_suffix(suffix) or "mp3"

    async def _user_delivery_prefs(self, user_id: int | None) -> UserDeliveryPrefs:
        if user_id is None:
            return delivery_prefs_from_profile(None)
        profile = await self._db.get_user_audio_profile(user_id)
        return delivery_prefs_from_profile(profile)

    async def _send_single_audio(
        self,
        bot: Bot,
        *,
        chat_id: int,
        prepared: PreparedAudio,
        thumb_path: Path | None,
    ):
        if prepared.as_document:
            return await self._send_single_document(bot, chat_id=chat_id, prepared=prepared)

        kwargs: dict = {
            "chat_id": chat_id,
            **_telegram_audio_metadata(prepared),
            "connect_timeout": TELEGRAM_CONNECT_TIMEOUT,
            "read_timeout": TELEGRAM_UPLOAD_TIMEOUT,
            "write_timeout": TELEGRAM_UPLOAD_TIMEOUT,
        }

        thumb_file = None
        if thumb_path and thumb_path.exists():
            thumb_file = thumb_path.open("rb")
            kwargs["thumbnail"] = InputFile(thumb_file, filename=COVER_FILENAME)

        try:
            if prepared.file_id:
                kwargs["audio"] = prepared.file_id
                message = await telegram_retry(
                    lambda: bot.send_audio(**kwargs),
                    description="send_audio",
                )
            elif prepared.file_path:
                with prepared.file_path.open("rb") as audio_file:
                    kwargs["audio"] = InputFile(audio_file, filename=prepared.filename)
                    message = await telegram_retry(
                        lambda: bot.send_audio(**kwargs),
                        description="send_audio",
                    )
            else:
                raise TranscodeError(NO_AUDIO_SOURCE_PREPARED)
        finally:
            if thumb_file:
                thumb_file.close()

        await self._cache_from_messages([message], [prepared])
        return message

    async def _send_single_document(
        self,
        bot: Bot,
        *,
        chat_id: int,
        prepared: PreparedAudio,
    ):
        caption = f"{prepared.track.artist} — {prepared.track.title}"
        kwargs: dict = {
            "chat_id": chat_id,
            "caption": caption[:1024],
            "connect_timeout": TELEGRAM_CONNECT_TIMEOUT,
            "read_timeout": TELEGRAM_UPLOAD_TIMEOUT,
            "write_timeout": TELEGRAM_UPLOAD_TIMEOUT,
        }
        if prepared.file_id:
            kwargs["document"] = prepared.file_id
            message = await telegram_retry(
                lambda: bot.send_document(**kwargs),
                description="send_document",
            )
        elif prepared.file_path:
            with prepared.file_path.open("rb") as doc_file:
                kwargs["document"] = InputFile(doc_file, filename=prepared.filename)
                message = await telegram_retry(
                    lambda: bot.send_document(**kwargs),
                    description="send_document",
                )
        else:
            raise TranscodeError(NO_AUDIO_SOURCE_PREPARED)

        await self._cache_from_messages([message], [prepared])
        return message

    async def _replace_message_with_audio(
        self,
        bot: Bot,
        *,
        chat_id: int,
        message_id: int,
        prepared: PreparedAudio,
        thumb_path: Path | None,
    ) -> None:
        if prepared.as_document:
            await self._replace_message_with_document(
                bot,
                chat_id=chat_id,
                message_id=message_id,
                prepared=prepared,
            )
            return

        media_kwargs: dict = _telegram_audio_metadata(prepared)

        file_id = prepared.file_id
        temp_upload_id: int | None = None

        if not file_id:
            if not prepared.file_path:
                raise TranscodeError(NO_AUDIO_SOURCE_PREPARED)
            upload_msg = await self._upload_audio_for_file_id(
                bot,
                chat_id=chat_id,
                prepared=prepared,
                thumb_path=thumb_path,
            )
            audio = upload_msg.audio
            if not audio:
                raise TranscodeError("telegram_upload_failed")
            file_id = audio.file_id
            temp_upload_id = upload_msg.message_id
            await self._cache_from_messages([upload_msg], [prepared])

        media = InputMediaAudio(media=file_id, **media_kwargs)
        try:
            message = await telegram_retry(
                lambda: bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=message_id,
                    media=media,
                ),
                description="edit_message_media",
            )
        except (BadRequest, NetworkError) as exc:
            logger.warning(
                "edit_message_media failed for message %s, using fallback: %s",
                message_id,
                exc,
            )
            try:
                await bot.delete_message(chat_id, message_id)
            except BadRequest:
                logger.debug("Could not delete loading message %s", message_id)
            if temp_upload_id is not None:
                return
            prepared.file_id = file_id
            await self._send_single_audio(
                bot,
                chat_id=chat_id,
                prepared=prepared,
                thumb_path=thumb_path,
            )
            return

        await self._cache_from_messages([message], [prepared])

        if temp_upload_id is not None:
            try:
                await bot.delete_message(chat_id, temp_upload_id)
            except BadRequest:
                logger.debug("Could not delete temp upload message %s", temp_upload_id)

    async def _replace_message_with_document(
        self,
        bot: Bot,
        *,
        chat_id: int,
        message_id: int,
        prepared: PreparedAudio,
    ) -> None:
        caption = f"{prepared.track.artist} — {prepared.track.title}"
        file_id = prepared.file_id
        temp_upload_id: int | None = None

        if not file_id:
            if not prepared.file_path:
                raise TranscodeError(NO_AUDIO_SOURCE_PREPARED)
            upload_msg = await self._send_single_document(
                bot,
                chat_id=chat_id,
                prepared=prepared,
            )
            document = upload_msg.document
            if not document:
                raise TranscodeError("telegram_upload_failed")
            file_id = document.file_id
            temp_upload_id = upload_msg.message_id

        media = InputMediaDocument(media=file_id, caption=caption[:1024])
        try:
            await telegram_retry(
                lambda: bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=message_id,
                    media=media,
                ),
                description="edit_message_media_document",
            )
        except (BadRequest, NetworkError) as exc:
            logger.warning(
                "edit_message_media (document) failed for message %s: %s",
                message_id,
                exc,
            )
            try:
                await bot.delete_message(chat_id, message_id)
            except BadRequest:
                logger.debug("Could not delete loading message %s", message_id)
            if temp_upload_id is None:
                prepared.file_id = file_id
                await self._send_single_document(
                    bot,
                    chat_id=chat_id,
                    prepared=prepared,
                )
            return

        if temp_upload_id is not None:
            try:
                await bot.delete_message(chat_id, temp_upload_id)
            except BadRequest:
                logger.debug("Could not delete temp upload message %s", temp_upload_id)

    async def _upload_audio_for_file_id(
        self,
        bot: Bot,
        *,
        chat_id: int,
        prepared: PreparedAudio,
        thumb_path: Path | None,
    ):
        kwargs: dict = {
            "chat_id": chat_id,
            **_telegram_audio_metadata(prepared),
            "connect_timeout": TELEGRAM_CONNECT_TIMEOUT,
            "read_timeout": TELEGRAM_UPLOAD_TIMEOUT,
            "write_timeout": TELEGRAM_UPLOAD_TIMEOUT,
        }
        if not prepared.file_path:
            raise TranscodeError(NO_AUDIO_SOURCE_PREPARED)

        thumb_file = None
        if thumb_path and thumb_path.exists():
            thumb_file = thumb_path.open("rb")
            kwargs["thumbnail"] = InputFile(thumb_file, filename=COVER_FILENAME)

        try:
            with prepared.file_path.open("rb") as audio_file:
                kwargs["audio"] = InputFile(audio_file, filename=prepared.filename)
                return await telegram_retry(
                    lambda: bot.send_audio(**kwargs),
                    description="upload_audio_for_file_id",
                )
        finally:
            if thumb_file:
                thumb_file.close()

    async def _send_header(
        self,
        bot: Bot,
        *,
        chat_id: int,
        cover_path: Path | None,
        caption: str,
    ) -> None:
        if cover_path and cover_path.exists():
            with cover_path.open("rb") as cover_file:
                await telegram_retry(
                    lambda: bot.send_photo(
                        chat_id=chat_id,
                        photo=InputFile(cover_file, filename=COVER_FILENAME),
                        caption=caption,
                        connect_timeout=TELEGRAM_CONNECT_TIMEOUT,
                        read_timeout=TELEGRAM_UPLOAD_TIMEOUT,
                        write_timeout=TELEGRAM_UPLOAD_TIMEOUT,
                    ),
                    description="send_album_cover",
                )
            return
        await telegram_retry(
            lambda: bot.send_message(chat_id=chat_id, text=caption),
            description="send_album_header",
        )

    async def _send_audio_group(
        self,
        bot: Bot,
        *,
        chat_id: int,
        prepared: list[PreparedAudio],
        thumb_path: Path | None = None,
        sent_before: int = 0,
        total_count: int | None = None,
        progress=None,
    ):
        if not prepared:
            return 0

        if len(prepared) == 1:
            await self._send_single_audio(bot, chat_id=chat_id, prepared=prepared[0], thumb_path=thumb_path)
            if progress and total_count is not None:
                await progress(f"Отправляю альбом... {sent_before + 1}/{total_count}")
            return 1

        with ExitStack() as stack:
            media = [self._audio_media(item, stack, thumb_path=thumb_path) for item in prepared]
            try:
                messages = await telegram_retry(
                    lambda: bot.send_media_group(
                        chat_id=chat_id,
                        media=media,
                        connect_timeout=TELEGRAM_CONNECT_TIMEOUT,
                        read_timeout=TELEGRAM_UPLOAD_TIMEOUT,
                        write_timeout=TELEGRAM_UPLOAD_TIMEOUT,
                    ),
                    description="send_media_group",
                )
            except (BadRequest, NetworkError, TimedOut) as exc:
                logger.error("send_media_group failed: %s", exc)
                return await self._send_audio_group_fallback(
                    bot,
                    chat_id=chat_id,
                    prepared=prepared,
                    thumb_path=thumb_path,
                )

        await self._cache_from_messages(messages, prepared)
        if progress and total_count is not None:
            await progress(f"Отправляю альбом... {sent_before + len(messages)}/{total_count}")
        return len(messages)

    async def _send_audio_group_fallback(
        self,
        bot: Bot,
        *,
        chat_id: int,
        prepared: list[PreparedAudio],
        thumb_path: Path | None,
    ) -> int:
        sent_count = 0
        for item in prepared:
            try:
                await self._send_single_audio(
                    bot,
                    chat_id=chat_id,
                    prepared=item,
                    thumb_path=thumb_path,
                )
                sent_count += 1
            except (NetworkError, TimedOut):
                logger.exception("send_audio fallback failed for %s", item.track.id)
        return sent_count

    def _audio_media(
        self,
        prepared: PreparedAudio,
        stack: ExitStack,
        *,
        thumb_path: Path | None,
    ) -> InputMediaAudio:
        kwargs: dict = _telegram_audio_metadata(prepared)
        if thumb_path and thumb_path.exists() and not prepared.file_id:
            thumb_file = stack.enter_context(thumb_path.open("rb"))
            kwargs["thumbnail"] = InputFile(thumb_file, filename=COVER_FILENAME)
        if prepared.file_id:
            return InputMediaAudio(media=prepared.file_id, **kwargs)
        if not prepared.file_path:
            raise TranscodeError(NO_AUDIO_SOURCE_PREPARED)
        handle = stack.enter_context(prepared.file_path.open("rb"))
        return InputMediaAudio(media=handle, filename=prepared.filename, **kwargs)

    async def _cache_from_messages(self, messages, prepared: list[PreparedAudio]) -> None:
        if not messages:
            return
        now = utc_now_iso()
        for message, item in zip(messages, prepared, strict=False):
            cache_payload = self._message_cache_payload(message, item)
            if cache_payload is None:
                continue
            file_id, file_unique_id, file_size, duration = cache_payload
            await self._db.save_cached_file_id(
                song_id=item.track.id,
                file_id=file_id,
                file_unique_id=file_unique_id,
                bitrate=item.cache_bitrate or self._settings.transcode_bitrate,
                file_size=file_size,
                duration=duration,
                title=item.track.title,
                artist=item.track.artist,
                album=item.track.album,
                created_at=now,
            )

    @staticmethod
    def _message_cache_payload(
        message,
        item: PreparedAudio,
    ) -> tuple[str, str, int | None, int | None] | None:
        if item.as_document:
            document = message.document
            if not document:
                return None
            return (
                document.file_id,
                document.file_unique_id,
                document.file_size,
                item.duration,
            )

        audio = message.audio
        if not audio:
            return None
        return (
            audio.file_id,
            audio.file_unique_id,
            audio.file_size,
            audio.duration or item.duration,
        )

    async def _load_cover(self, track: Track, key: str) -> Path | None:
        cover_art_id = track.cover_art_id
        if not cover_art_id:
            logger.info("No cover_art_id for track %s, skipping cover", key)
            return None
        cover_path = self._settings.temp_dir / f"{key}.cover.jpg"
        if await self._navidrome.download_cover_art(
            cover_art_id, cover_path, size=THUMBNAIL_COVER_SIZE
        ):
            return cover_path
        return None

    async def _load_cover_for_album(self, album: Album, album_id: str) -> Path | None:
        if not album.cover_art_id:
            return None
        return await self._load_cover(
            Track(
                id=album_id,
                title=album.title,
                artist=album.artist,
                album=album.title,
                cover_art_id=album.cover_art_id,
            ),
            album_id,
        )

    async def _prepare_audio_file(
        self,
        source_path: Path,
        output_path: Path,
        track: Track,
        *,
        prefs: UserDeliveryPrefs,
        source_suffix: str | None,
        source_bitrate_kbps: int | None,
        cover_path: Path | None = None,
    ) -> tuple[Path, _ResolvedDelivery]:
        has_cover = cover_path is not None and cover_path.exists()
        normalized_suffix = normalize_suffix(source_suffix or source_path.suffix)

        if should_send_original(source_suffix, source_bitrate_kbps, prefs):
            extension = normalized_suffix or track.suffix or "mp3"
            return source_path, _ResolvedDelivery(
                use_original=True,
                cache_bitrate=source_cache_profile(
                    extension,
                    source_bitrate_kbps,
                    transcoded_with_cover=has_cover,
                ),
                file_extension=extension,
                as_document=send_as_document(extension),
            )

        if prefs.format == "flac":
            await self._transcoder.transcode(
                source_path,
                output_path,
                track=track,
                cover_path=cover_path,
                audio_format="flac",
            )
            return output_path, _ResolvedDelivery(
                use_original=False,
                cache_bitrate=prefs.cache_profile(with_cover=has_cover),
                file_extension="flac",
                as_document=True,
            )

        if prefs.format == "opus":
            await self._transcoder.transcode(
                source_path,
                output_path,
                track=track,
                cover_path=cover_path,
                audio_format="opus",
                bitrate=prefs.bitrate or "256k",
            )
            return output_path, _ResolvedDelivery(
                use_original=False,
                cache_bitrate=prefs.cache_profile(with_cover=has_cover),
                file_extension="ogg",
                as_document=False,
            )

        await self._transcoder.transcode(
            source_path,
            output_path,
            track=track,
            cover_path=cover_path,
            audio_format="mp3",
            bitrate=prefs.bitrate or self._settings.transcode_bitrate,
        )
        return output_path, _ResolvedDelivery(
            use_original=False,
            cache_bitrate=prefs.cache_profile(with_cover=has_cover),
            file_extension="mp3",
            as_document=False,
        )

    def _navidrome_stream_bitrate(self, prefs: UserDeliveryPrefs) -> int | None:
        if not prefs.bitrate:
            return self._settings.navidrome_stream_max_bitrate
        return parse_bitrate_kbps(prefs.bitrate) or self._settings.navidrome_stream_max_bitrate

    @staticmethod
    def _audio_filename(track: Track, *, extension: str) -> str:
        ext = extension.lstrip(".")
        raw = f"{track.artist} - {track.title}.{ext}"
        safe = re.sub(r'[<>:"/\\|?*]', "_", raw).strip(" .")
        fallback = f"track.{ext}"
        return safe[:200] if safe else fallback

    @staticmethod
    def _cleanup(*paths: Path | None) -> None:
        for path in paths:
            if path and path.exists():
                path.unlink(missing_ok=True)

    @staticmethod
    @asynccontextmanager
    async def _timed_step(timer: PrepTimer | None, name: str):
        if timer is None:
            yield
            return
        async with timer.step(name):
            yield

    async def _log_prep_timing(
        self,
        timer: PrepTimer | None,
        *,
        user_id: int | None,
        song_id: str,
    ) -> None:
        if timer is None:
            return
        try:
            timer.log_summary()
            if not self._settings.prep_timing_log_events:
                return
            await self._db.log_event(
                user_id=user_id,
                song_id=song_id,
                event_type="prep_timing",
                context=timer.to_event_context(),
                created_at=utc_now_iso(),
            )
        except Exception:
            logger.exception("Failed to log prep timing for %s", song_id)
