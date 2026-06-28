import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.handlers.random import random_command
from bot.handlers.search import run_search
from bot.handlers.start import help_command
from bot.keyboards.menu import (
    BTN_HELP,
    BTN_RADIO_LAST,
    BTN_RANDOM,
    BTN_SETTINGS,
    MENU_BUTTONS,
    main_menu_keyboard,
)
from bot.services.navidrome import NavidromeError
from bot.utils.access import deny_if_not_allowed
from bot.utils.track_pages import LAST_TRACK_SEED_KEY, LAST_TRACK_TITLE_KEY

logger = logging.getLogger(__name__)

MENU_TEXT = "Меню ниже. Просто напиши запрос — это поиск."


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await deny_if_not_allowed(update, context):
        return
    await update.effective_message.reply_text(MENU_TEXT, reply_markup=main_menu_keyboard())


async def menu_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await deny_if_not_allowed(update, context):
        return

    message = update.effective_message
    if not message or not message.text:
        return

    text = message.text.strip()

    if text == BTN_RANDOM:
        await random_command(update, context)
        return

    if text == BTN_HELP:
        await help_command(update, context)
        return

    if text == BTN_SETTINGS:
        from bot.handlers.settings import settings_command

        await settings_command(update, context)
        return

    from bot.handlers.settings import settings_message_handler

    if await settings_message_handler(update, context):
        return

    if text == BTN_RADIO_LAST:
        song_id = context.user_data.get(LAST_TRACK_SEED_KEY)
        title = context.user_data.get(LAST_TRACK_TITLE_KEY)
        if not song_id or not title:
            await message.reply_text(
                "Сначала выбери трек — нажми 📥 Получить или 📻 Радио на карточке.",
                reply_markup=main_menu_keyboard(),
            )
            return

        from bot.handlers.callbacks import send_radio_page

        try:
            await send_radio_page(
                update,
                context,
                song_id=song_id,
                source_title=title,
            )
        except NavidromeError:
            await message.reply_text("Navidrome сейчас недоступен.")
        return

    if text in MENU_BUTTONS:
        return

    await run_search(update, context, text)
