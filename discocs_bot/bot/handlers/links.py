"""Links dropped into the chat: show what is behind them, then fetch on demand.

A link is answered with a card first and downloaded only when asked. Metadata
costs one cheap request; the download costs traffic and tens of seconds, so
guessing which one the person wanted would be wrong half the time.
"""
from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from bot.keyboards.external import external_link_keyboard
from bot.services.external_audio import ExternalAudioError, ExternalTrackInfo
from bot.storage.models import utc_now_iso
from bot.utils.access import deny_if_not_allowed
from bot.utils.links import UnsafeLinkError, find_first_url, validate_public_url

logger = logging.getLogger(__name__)

LOOKUP_STATUS = "🔍 Смотрю ссылку..."
DOWNLOAD_STATUS = "⬇️ Качаю..."
PREPARE_STATUS = "🎛 Готовлю MP3..."
UPLOAD_STATUS = "📤 Отправляю..."
LINK_EXPIRED = "Ссылка устарела — пришли её ещё раз."


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


async def _safe_edit(message, text: str) -> None:
    try:
        await message.edit_text(text)
    except BadRequest:
        logger.debug("Could not edit status message %s", message.message_id)


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
    db = context.bot_data["db"]

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

    now = utc_now_iso()
    await db.save_external_media(
        media_key=info.media_key,
        url_key=info.url_key,
        source=info.source,
        webpage_url=info.webpage_url or url,
        title=info.title,
        artist=info.artist,
        duration=info.duration,
        thumbnail_url=info.thumbnail_url,
        now=now,
    )
    await db.log_event(
        user_id=update.effective_user.id if update.effective_user else None,
        song_id=None,
        event_type="external_link",
        context=info.url_key,
        created_at=now,
    )

    caption = card_caption(info)
    keyboard = external_link_keyboard(info.media_key)
    await _delete_quietly(status)
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


async def _delete_quietly(message) -> None:
    try:
        await message.delete()
    except BadRequest:
        logger.debug("Could not delete status message")


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
        source_path = await delivery.source_file(row["webpage_url"], info)
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
