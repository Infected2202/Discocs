import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards.menu import main_menu_keyboard
from bot.services.navidrome import NavidromeError
from bot.utils.access import deny_if_not_allowed
from bot.utils.track_cards import reply_track_card
from bot.utils.track_pages import remember_last_track

logger = logging.getLogger(__name__)


async def random_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await deny_if_not_allowed(update, context):
        return

    navidrome = context.bot_data["navidrome"]
    settings = context.bot_data["settings"]
    message = update.effective_message

    try:
        tracks = await navidrome.get_random_tracks(limit=1)
    except NavidromeError:
        logger.exception("Navidrome random failed")
        await message.reply_text("Navidrome сейчас недоступен.", reply_markup=main_menu_keyboard())
        return

    if not tracks:
        await message.reply_text(
            "Не удалось получить случайный трек.",
            reply_markup=main_menu_keyboard(),
        )
        return

    track = tracks[0]
    remember_last_track(context, song_id=track.id, title=track.display_line)

    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    await reply_track_card(
        message,
        track,
        navidrome=navidrome,
        temp_dir=settings.temp_dir,
    )
