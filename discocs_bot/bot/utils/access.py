import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import Settings

logger = logging.getLogger(__name__)

DENIED_MESSAGE = "У тебя пока нет доступа к этому боту."


def is_allowed(user_id: int | None, _settings: Settings) -> bool:
    return user_id is not None


async def deny_if_not_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    settings: Settings = context.bot_data["settings"]
    user = update.effective_user
    if is_allowed(user.id if user else None, settings):
        return False

    logger.info("denied access for user_id=%s", user.id if user else None)
    if update.callback_query:
        await update.callback_query.answer(DENIED_MESSAGE, show_alert=True)
    elif update.effective_message:
        await update.effective_message.reply_text(DENIED_MESSAGE)
    return True
