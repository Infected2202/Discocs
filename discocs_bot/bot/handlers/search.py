import logging
from pathlib import Path

from telegram import Message, Update
from telegram.ext import ContextTypes

from bot.services.navidrome import NavidromeError
from bot.storage.models import utc_now_iso
from bot.utils.access import deny_if_not_allowed
from bot.utils.track_pages import (
    SEARCH_QUERY_KEY,
    send_track_results_page,
)

logger = logging.getLogger(__name__)


def audio_search_query(message: Message) -> str | None:
    audio = message.audio
    if not audio:
        return None

    parts: list[str] = []
    if audio.performer:
        parts.append(audio.performer.strip())
    if audio.title:
        parts.append(audio.title.strip())
    if parts:
        return " ".join(parts)

    if audio.file_name:
        stem = Path(audio.file_name).stem.strip()
        if stem:
            return stem
    return None


async def audio_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await deny_if_not_allowed(update, context):
        return

    message = update.effective_message
    if not message:
        return

    query = audio_search_query(message)
    if not query:
        await message.reply_text("Не удалось определить название трека.")
        return

    await run_search(update, context, query)


async def _send_search_page(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    query: str,
) -> None:
    navidrome = context.bot_data["navidrome"]
    settings = context.bot_data["settings"]
    message = update.effective_message
    if not message:
        return

    page_size = settings.discocs_count
    try:
        tracks, has_next = await navidrome.search_tracks(query, limit=page_size, offset=0)
    except NavidromeError:
        logger.exception("Navidrome search failed")
        await message.reply_text("Navidrome сейчас недоступен.")
        return

    if not tracks:
        await message.reply_text("Ничего не нашел по запросу.\nПопробуй изменить формулировку.")
        return

    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    header = f'Поиск: «{query}»'

    await send_track_results_page(
        message,
        tracks,
        context=context,
        navidrome=navidrome,
        temp_dir=settings.temp_dir,
        header=header,
        page_size=page_size,
        page_kind="search",
        session_key=query,
        has_next=has_next,
    )


async def run_search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: str,
) -> None:
    db = context.bot_data["db"]
    user = update.effective_user

    context.user_data[SEARCH_QUERY_KEY] = query

    await db.log_event(
        user_id=user.id if user else None,
        song_id=None,
        event_type="search",
        context=query,
        created_at=utc_now_iso(),
    )

    await _send_search_page(update, context, query=query)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await deny_if_not_allowed(update, context):
        return

    query = " ".join(context.args).strip()
    if not query:
        from bot.keyboards.menu import main_menu_keyboard

        await update.effective_message.reply_text(
            "Введи запрос в чат.",
            reply_markup=main_menu_keyboard(),
        )
        return

    await run_search(update, context, query)


async def search_page_callback(_update: Update, context: ContextTypes.DEFAULT_TYPE, offset: int) -> None:
    from bot.utils.track_pages import move_results_slot

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
