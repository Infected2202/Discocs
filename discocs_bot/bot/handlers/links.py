"""External seeds: links dropped into the chat and audio files sent to it.

A link is answered with a card first and downloaded only when asked. Metadata
costs one cheap request; the download costs traffic and tens of seconds, so
guessing which one the person wanted would be wrong half the time.

Both kinds of seed end up in the same two places: an MP3 in the chat, or radio
built from the audio itself against the library.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from telegram import Message, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from bot.keyboards.external import (
    external_audio_keyboard,
    external_link_keyboard,
    library_match_keyboard,
)
from bot.services.discocs import DiscocsError
from bot.services.external_audio import (
    ExternalAudioError,
    ExternalTrackInfo,
    media_key_for,
    split_artist_title,
)
from bot.services.navidrome import NavidromeError
from bot.storage.models import utc_now_iso
from bot.utils.access import deny_if_not_allowed
from bot.utils.library_match import find_match, search_query
from bot.utils.links import UnsafeLinkError, find_first_url, validate_public_url
from bot.utils.track_cards import send_track_card
from bot.utils.track_pages import remember_last_track, send_track_results_page

logger = logging.getLogger(__name__)

LOOKUP_STATUS = "🔍 Смотрю ссылку..."
DOWNLOAD_STATUS = "⬇️ Качаю..."
PREPARE_STATUS = "🎛 Готовлю MP3..."
UPLOAD_STATUS = "📤 Отправляю..."
ANALYZE_STATUS = "🎛 Анализирую звук..."
RADIO_STATUS = "📻 Строю радио..."
LINK_EXPIRED = "Ссылка устарела — пришли её ещё раз."
IN_LIBRARY_NOTE = "✅ Этот трек уже есть в библиотеке"
NO_RADIO = "Не нашёл ничего похожего в библиотеке."

# Telegram hands a bot files up to 20 MB; radio needs the actual bytes.
TELEGRAM_DOWNLOAD_LIMIT_BYTES = 20 * 1024 * 1024
LIBRARY_SEARCH_LIMIT = 5


def format_duration(seconds: int | None) -> str:
    if not seconds:
        return ""
    minutes, remainder = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{remainder:02d}"
    return f"{minutes}:{remainder:02d}"


def card_caption(info: ExternalTrackInfo) -> str:
    lines = [f"🔗 {info.source}", info.display_line]
    duration = format_duration(info.duration)
    if duration:
        lines.append(duration)
    return "\n".join(lines)


def audio_caption(info: ExternalTrackInfo, *, can_analyze: bool) -> str:
    lines = ["🎧 Файл", info.display_line]
    duration = format_duration(info.duration)
    if duration:
        lines.append(duration)
    if not can_analyze:
        lines.append("Больше 20 МБ — Telegram не отдаёт такой файл боту, радио по звуку недоступно.")
    return "\n".join(lines)


def info_from_row(row) -> ExternalTrackInfo:
    return ExternalTrackInfo(
        media_key=row["media_key"],
        url_key=row["url_key"],
        source=row["source"],
        webpage_url=row["webpage_url"],
        title=row["title"],
        artist=row["artist"],
        duration=row["duration"],
        thumbnail_url=row["thumbnail_url"],
    )


def info_from_audio(audio, file_unique_id: str) -> ExternalTrackInfo:
    """What a Telegram audio message tells us about the track."""
    artist = (audio.performer or "").strip() or None
    title = (audio.title or "").strip()
    if not title:
        # Untagged files carry their metadata in the name, extension and all.
        artist, title = split_artist_title(Path(audio.file_name or "Аудио").stem, artist)
    url_key = f"telegram:{file_unique_id}"
    return ExternalTrackInfo(
        media_key=media_key_for(url_key),
        url_key=url_key,
        source="telegram",
        webpage_url="",
        title=title,
        artist=artist,
        duration=audio.duration,
    )


async def _safe_edit(message, text: str) -> None:
    try:
        await message.edit_text(text)
    except BadRequest:
        logger.debug("Could not edit status message %s", message.message_id)


async def _delete_quietly(message) -> None:
    try:
        await message.delete()
    except BadRequest:
        logger.debug("Could not delete status message")


async def _remember(context: ContextTypes.DEFAULT_TYPE, info: ExternalTrackInfo, **extra) -> None:
    await context.bot_data["db"].save_external_media(
        media_key=info.media_key,
        url_key=info.url_key,
        source=info.source,
        webpage_url=info.webpage_url,
        title=info.title,
        artist=info.artist,
        duration=info.duration,
        thumbnail_url=info.thumbnail_url,
        now=utc_now_iso(),
        **extra,
    )


async def _library_match(context: ContextTypes.DEFAULT_TYPE, info: ExternalTrackInfo):
    """The library's own copy of a linked track, when it has one.

    Worth a search before every download: the library copy has an embedding
    from the original file, and nothing has to be fetched at all.
    """
    navidrome = context.bot_data["navidrome"]
    try:
        tracks, _has_next = await navidrome.search_tracks(
            search_query(info.artist, info.title),
            limit=LIBRARY_SEARCH_LIMIT,
        )
    except NavidromeError:
        logger.warning("Library lookup failed for %s", info.url_key)
        return None
    return find_match(tracks, artist=info.artist, title=info.title)


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------

async def link_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await deny_if_not_allowed(update, context):
        return

    message = update.effective_message
    if not message:
        return
    url = find_first_url(message.text or message.caption)
    if not url:
        return

    links = context.bot_data["links"]
    status = await message.reply_text(LOOKUP_STATUS)
    try:
        url = await asyncio.to_thread(validate_public_url, url)
        info = await links.fetch_info(url)
    except UnsafeLinkError as exc:
        logger.warning("Rejected link: %s", exc)
        await _safe_edit(status, exc.user_message)
        return
    except ExternalAudioError as exc:
        await _safe_edit(status, exc.user_message)
        return
    except Exception:
        logger.exception("Link lookup failed url=%s", url)
        await _safe_edit(status, "Не удалось прочитать ссылку.")
        return

    if not info.webpage_url:
        info.webpage_url = url
    await _remember(context, info)
    await context.bot_data["db"].log_event(
        user_id=update.effective_user.id if update.effective_user else None,
        song_id=None,
        event_type="external_link",
        context=info.url_key,
        created_at=utc_now_iso(),
    )

    match = await _library_match(context, info)
    await _delete_quietly(status)
    if match is not None:
        await _send_library_card(context, message, match, info)
        return
    await _send_link_card(message, info)


async def _send_link_card(message: Message, info: ExternalTrackInfo) -> None:
    caption = card_caption(info)
    keyboard = external_link_keyboard(info.media_key)
    if info.thumbnail_url:
        try:
            await message.reply_photo(
                photo=info.thumbnail_url,
                caption=caption,
                reply_markup=keyboard,
            )
            return
        except BadRequest:
            logger.debug("Could not send link preview photo for %s", info.media_key)
    await message.reply_text(caption, reply_markup=keyboard)


async def _send_library_card(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
    track,
    info: ExternalTrackInfo,
) -> None:
    settings = context.bot_data["settings"]
    navidrome = context.bot_data["navidrome"]
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    remember_last_track(context, song_id=track.id, title=track.display_line)
    keyboard = library_match_keyboard(track.id, info.media_key, track.album_id)
    await send_track_card(
        message.get_bot(),
        message.chat_id,
        track,
        navidrome=navidrome,
        temp_dir=settings.temp_dir,
        caption_prefix=IN_LIBRARY_NOTE,
        keyboard=keyboard,
    )


# ---------------------------------------------------------------------------
# Audio files
# ---------------------------------------------------------------------------

async def audio_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await deny_if_not_allowed(update, context):
        return

    message = update.effective_message
    if not message or not message.audio:
        return
    audio = message.audio
    info = info_from_audio(audio, audio.file_unique_id)
    can_analyze = not audio.file_size or audio.file_size <= TELEGRAM_DOWNLOAD_LIMIT_BYTES

    await _remember(context, info, telegram_file_id=audio.file_id)
    await message.reply_text(
        audio_caption(info, can_analyze=can_analyze),
        reply_markup=external_audio_keyboard(info.media_key, can_analyze=can_analyze),
    )


async def external_search_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    media_key: str,
) -> None:
    from bot.handlers.search import run_search

    message = update.effective_message
    if not message:
        return
    row = await context.bot_data["db"].get_external_media(media_key)
    if row is None:
        await message.reply_text(LINK_EXPIRED)
        return
    info = info_from_row(row)
    await run_search(update, context, search_query(info.artist, info.title))


# ---------------------------------------------------------------------------
# Delivery and radio
# ---------------------------------------------------------------------------

async def _source_path(context: ContextTypes.DEFAULT_TYPE, row, info: ExternalTrackInfo):
    delivery = context.bot_data["external_delivery"]
    if row["source"] == "telegram":
        file_id = row["telegram_file_id"]
        if not file_id:
            raise ExternalAudioError(
                "Telegram media without a file id",
                user_message="Пришли файл ещё раз — я потерял ссылку на него.",
            )
        return await delivery.telegram_source_file(context.bot, file_id, info)
    return await delivery.source_file(row["webpage_url"], info)


async def external_get_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    media_key: str,
) -> None:
    message = update.effective_message
    if not message:
        return

    db = context.bot_data["db"]
    delivery = context.bot_data["external_delivery"]
    settings = context.bot_data["settings"]

    row = await db.get_external_media(media_key)
    if row is None:
        await message.reply_text(LINK_EXPIRED)
        return
    info = info_from_row(row)

    status = await message.reply_text(DOWNLOAD_STATUS)
    cached_parts = await db.get_external_parts(media_key)
    if cached_parts:
        await _safe_edit(status, UPLOAD_STATUS)
        if await delivery.send_cached(context.bot, chat_id=message.chat_id, parts=cached_parts):
            await db.touch_external_media(media_key, utc_now_iso())
            await _delete_quietly(status)
            return

    prepared = None
    try:
        source_path = await _source_path(context, row, info)
        await _safe_edit(status, PREPARE_STATUS)
        prepared = await delivery.prepare_mp3(info, source_path, settings.temp_dir)
        await _safe_edit(status, UPLOAD_STATUS)
        sent = await delivery.send_prepared(
            context.bot,
            chat_id=message.chat_id,
            prepared=prepared,
        )
        await delivery.remember_delivery(info, profile=prepared.profile, parts=sent)
        await db.log_event(
            user_id=update.effective_user.id if update.effective_user else None,
            song_id=None,
            event_type="external_send",
            context=info.url_key,
            created_at=utc_now_iso(),
        )
        await _delete_quietly(status)
    except ExternalAudioError as exc:
        await _safe_edit(status, exc.user_message)
    except Exception:
        logger.exception("External delivery failed media_key=%s", media_key)
        await _safe_edit(status, "Не удалось отправить аудио.")
    finally:
        if prepared is not None:
            prepared.cleanup()


async def external_radio_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    media_key: str,
) -> None:
    message = update.effective_message
    if not message:
        return

    db = context.bot_data["db"]
    discocs = context.bot_data["discocs"]
    navidrome = context.bot_data["navidrome"]
    settings = context.bot_data["settings"]

    row = await db.get_external_media(media_key)
    if row is None:
        await message.reply_text(LINK_EXPIRED)
        return
    info = info_from_row(row)

    status = await message.reply_text(
        ANALYZE_STATUS if row["source"] == "telegram" else DOWNLOAD_STATUS
    )
    try:
        source_path = await _source_path(context, row, info)
        await _safe_edit(status, ANALYZE_STATUS)
        tracks = await discocs.get_similar_by_audio(source_path)
    except ExternalAudioError as exc:
        await _safe_edit(status, exc.user_message)
        return
    except DiscocsError as exc:
        await _safe_edit(status, exc.user_message)
        return
    except Exception:
        logger.exception("External radio failed media_key=%s", media_key)
        await _safe_edit(status, "Не удалось построить радио.")
        return

    if not tracks:
        await _safe_edit(status, NO_RADIO)
        return

    await _safe_edit(status, RADIO_STATUS)
    await db.log_event(
        user_id=update.effective_user.id if update.effective_user else None,
        song_id=None,
        event_type="external_radio",
        context=info.url_key,
        created_at=utc_now_iso(),
    )
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    await _delete_quietly(status)
    # No paging: the seed lives outside the library, so another page would mean
    # uploading and embedding the audio again for results we already asked for.
    await send_track_results_page(
        message,
        tracks,
        context=context,
        navidrome=navidrome,
        temp_dir=settings.temp_dir,
        header=f"Радио от {info.display_line}",
        page_size=len(tracks),
        page_kind="external_radio",
        session_key=info.media_key,
        has_next=False,
    )
