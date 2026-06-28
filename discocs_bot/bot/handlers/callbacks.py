import logging

from telegram import Update
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.ext import ContextTypes

from bot.handlers.search import search_page_callback
from bot.services.discocs import DiscocsError
from bot.services.delivery import DeliveryService
from bot.services.navidrome import NavidromeError
from bot.services.transcoder import TranscodeError
from bot.storage.models import utc_now_iso
from bot.utils.access import deny_if_not_allowed
from bot.utils.telegram_retry import telegram_retry
from bot.keyboards.track import track_keyboard
from bot.utils.track_cards import edit_track_card, send_track_loading_card, track_card_caption
from bot.utils.track_pages import (
    RADIO_SEED_KEY,
    RADIO_TITLE_KEY,
    get_results_view,
    move_results_slot,
    remember_last_track,
    restore_previous_results_view,
    send_track_results_page,
    show_carousel_slot,
)

logger = logging.getLogger(__name__)

LOADING_CAPTION = "⏳ Готовлю..."
ERROR_DETAIL_LIMIT = 600


async def _safe_edit_text(message, text: str) -> None:
    try:
        await telegram_retry(
            lambda: message.edit_text(text[:ERROR_DETAIL_LIMIT]),
            description="edit_status_message",
        )
    except BadRequest:
        logger.debug("Could not edit status message")
    except (TimedOut, NetworkError):
        logger.warning("Could not edit status message after retries")


async def _safe_edit_loading_message(message, text: str) -> None:
    try:
        if message.photo:
            await message.edit_caption(caption=text[:ERROR_DETAIL_LIMIT])
        else:
            await message.edit_text(text=text[:ERROR_DETAIL_LIMIT])
    except BadRequest:
        logger.debug("Could not edit loading message %s", message.message_id)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await deny_if_not_allowed(update, context):
        return

    query = update.callback_query
    if not query or not query.data:
        return

    try:
        await telegram_retry(lambda: query.answer(), description="answer_callback_query")
    except (TimedOut, NetworkError):
        logger.warning("Could not answer callback query %s", query.data)
    action, _, payload = query.data.partition(":")
    if not payload and action not in ("get", "album", "radio", "send", "noop", "result_back"):
        return

    if action == "noop":
        return
    if action == "result_back":
        await result_back_callback(update, context)
        return
    if action == "pref":
        from bot.handlers.settings import pref_callback

        profile = query.data.split(":", 1)[1]
        await pref_callback(update, context, profile)
        return
    if action in ("get", "send"):
        context.application.create_task(_handle_get(update, context, payload))
        return
    if action == "album":
        context.application.create_task(_handle_album(update, context, payload))
        return
    if action == "radio":
        context.application.create_task(_handle_radio(update, context, payload))
        return
    if action == "search_page":
        await search_page_callback(update, context, int(payload))
    elif action == "radio_page":
        await radio_page_callback(update, context, int(payload))
    elif action == "result_move":
        await result_move_callback(update, context, int(payload))


async def _set_card_loading(query) -> None:
    if not query.message:
        return
    try:
        if query.message.photo:
            await query.edit_message_caption(caption=LOADING_CAPTION, reply_markup=None)
        else:
            await query.edit_message_text(text=LOADING_CAPTION, reply_markup=None)
    except BadRequest as exc:
        logger.debug("Could not set loading state on message %s: %s", query.message.message_id, exc)


async def _set_card_error(query, text: str) -> None:
    try:
        if query.message and query.message.photo:
            await query.edit_message_caption(caption=text, reply_markup=None)
            return
    except BadRequest:
        pass
    try:
        await query.edit_message_text(text=text, reply_markup=None)
    except BadRequest:
        logger.debug("Could not set error on message %s", query.message.message_id if query.message else None)


async def _restore_card_after_error(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    song_id: str,
) -> None:
    query = update.callback_query
    navidrome = context.bot_data["navidrome"]
    settings = context.bot_data["settings"]
    if not query or not query.message:
        return
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    view = get_results_view(context)
    if view and view.message_id == query.message.message_id:
        await show_carousel_slot(
            context,
            context.bot,
            slot=view.slot,
            navidrome=navidrome,
            temp_dir=settings.temp_dir,
        )
        return
    try:
        track = await navidrome.get_song(song_id)
    except NavidromeError:
        await _set_card_error(query, "Navidrome сейчас недоступен.")
        return
    if not await edit_track_card(
        context.bot,
        query.message.chat_id,
        query.message.message_id,
        track,
        navidrome=navidrome,
        temp_dir=settings.temp_dir,
    ):
        caption = track_card_caption(track)
        keyboard = track_keyboard(track.id, track.album_id)
        try:
            if query.message.photo:
                await query.edit_message_caption(caption=caption, reply_markup=keyboard)
            else:
                await query.edit_message_text(text=caption, reply_markup=keyboard)
        except BadRequest:
            logger.debug("Could not restore card for song %s", song_id)


async def _handle_get(update: Update, context: ContextTypes.DEFAULT_TYPE, song_id: str) -> None:
    delivery: DeliveryService = context.bot_data["delivery"]
    navidrome = context.bot_data["navidrome"]
    query = update.callback_query
    user = update.effective_user
    if not query or not query.message:
        return

    message = query.message
    settings = context.bot_data["settings"]
    settings.temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        track = await navidrome.get_song(song_id)
        remember_last_track(context, song_id=song_id, title=track.display_line)
    except NavidromeError:
        await message.reply_text("Navidrome сейчас недоступен.")
        return

    try:
        loading = await send_track_loading_card(
            context.bot,
            message.chat_id,
            track,
            navidrome=navidrome,
            temp_dir=settings.temp_dir,
            caption=LOADING_CAPTION,
        )
    except Exception:
        logger.exception("Could not send loading card for song_id=%s", song_id)
        await message.reply_text("Не удалось создать сообщение загрузки.")
        return

    try:
        await delivery.deliver_track_to_message(
            context.bot,
            chat_id=loading.chat_id,
            message_id=loading.message_id,
            song_id=song_id,
            user_id=user.id if user else None,
        )
    except NavidromeError:
        await _safe_edit_loading_message(loading, "Navidrome сейчас недоступен.")
    except TranscodeError as exc:
        if str(exc) == "file_too_large":
            await _safe_edit_loading_message(
                loading,
                "Файл слишком большой для отправки в Telegram.",
            )
        elif str(exc) in ("telegram_replace_failed", "telegram_upload_failed"):
            await _safe_edit_loading_message(loading, "Не удалось заменить сообщение загрузки на аудио.")
        else:
            logger.error("deliver_track_to_message failed: %s", exc)
            await _safe_edit_loading_message(loading, "Не удалось подготовить аудио.")
    except Exception:
        logger.exception("deliver_track_to_message failed for song_id=%s", song_id)
        await _safe_edit_loading_message(loading, "Не удалось получить трек.")


async def _handle_album(update: Update, context: ContextTypes.DEFAULT_TYPE, album_id: str) -> None:
    delivery: DeliveryService = context.bot_data["delivery"]
    message = update.effective_message
    user = update.effective_user
    if not message:
        return

    status = await telegram_retry(
        lambda: message.reply_text("Готовлю альбом... 0/?"),
        description="reply_album_status",
    )
    try:
        await delivery.deliver_album(
            context.bot,
            chat_id=message.chat_id,
            album_id=album_id,
            user_id=user.id if user else None,
            status_message=status,
        )
        await telegram_retry(lambda: status.delete(), description="delete_album_status")
    except NavidromeError:
        await _safe_edit_text(status, "Navidrome сейчас недоступен.")
    except TranscodeError as exc:
        if str(exc) == "file_too_large":
            await _safe_edit_text(
                status,
                "Один из треков слишком большой для отправки в Telegram."
            )
        else:
            logger.error("deliver_album failed: %s", exc)
            await _safe_edit_text(status, f"Не удалось отправить альбом в Telegram.\n{exc}")
    except (TimedOut, NetworkError):
        logger.exception("deliver_album timed out for album_id=%s", album_id)
        await _safe_edit_text(status, "Telegram не ответил вовремя. Попробуй ещё раз.")
    except Exception:
        logger.exception("deliver_album failed for album_id=%s", album_id)
        await _safe_edit_text(status, "Не удалось получить альбом.")


async def result_move_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, slot: int) -> None:
    settings = context.bot_data["settings"]
    navidrome = context.bot_data["navidrome"]
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    await move_results_slot(
        context,
        context.bot,
        target_slot=slot,
        navidrome=navidrome,
        temp_dir=settings.temp_dir,
    )


async def result_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.bot_data["settings"]
    navidrome = context.bot_data["navidrome"]
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    await restore_previous_results_view(
        context,
        context.bot,
        navidrome=navidrome,
        temp_dir=settings.temp_dir,
    )


async def send_radio_page(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    song_id: str,
    source_title: str,
) -> None:
    navidrome = context.bot_data["navidrome"]
    discocs = context.bot_data["discocs"]
    settings = context.bot_data["settings"]
    message = update.effective_message
    if not message:
        return

    page_size = settings.discocs_count
    status = None
    view = get_results_view(context)
    if (
        not view
        or view.kind != "radio"
        or view.session_key != song_id
        or view.chat_id != message.chat_id
    ):
        status = await message.reply_text("Строю радио...")

    try:
        tracks, has_next = await discocs.get_similar_tracks(
            song_id, offset=0, limit=page_size
        )
    except NavidromeError:
        if status:
            await status.edit_text("Navidrome сейчас недоступен.")
        else:
            await message.reply_text("Navidrome сейчас недоступен.")
        return
    except DiscocsError as exc:
        if status:
            await status.edit_text(exc.user_message)
        else:
            await message.reply_text(exc.user_message)
        return

    if status:
        await status.delete()

    if not tracks:
        await message.reply_text(
            "Не удалось построить радио для этого трека.\n"
            "Возможно, трек еще не проиндексирован в Discocs."
        )
        return

    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    title = f"Радио от {source_title}"
    await send_track_results_page(
        message,
        tracks,
        context=context,
        navidrome=navidrome,
        temp_dir=settings.temp_dir,
        header=title,
        page_size=page_size,
        page_kind="radio",
        session_key=song_id,
        has_next=has_next,
    )


async def _handle_radio(update: Update, context: ContextTypes.DEFAULT_TYPE, song_id: str) -> None:
    navidrome = context.bot_data["navidrome"]
    db = context.bot_data["db"]
    message = update.effective_message
    user = update.effective_user
    if not message:
        return

    try:
        source = await navidrome.get_song(song_id)
    except NavidromeError:
        await message.reply_text("Navidrome сейчас недоступен.")
        return

    context.user_data[RADIO_SEED_KEY] = song_id
    context.user_data[RADIO_TITLE_KEY] = source.display_line
    remember_last_track(context, song_id=song_id, title=source.display_line)

    await db.log_event(
        user_id=user.id if user else None,
        song_id=song_id,
        event_type="radio",
        context=None,
        created_at=utc_now_iso(),
    )

    await send_radio_page(
        update,
        context,
        song_id=song_id,
        source_title=source.display_line,
    )


async def radio_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, offset: int) -> None:
    settings = context.bot_data["settings"]
    navidrome = context.bot_data["navidrome"]
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    await move_results_slot(
        context,
        context.bot,
        target_slot=offset,
        navidrome=navidrome,
        temp_dir=settings.temp_dir,
    )
