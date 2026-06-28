import contextlib

from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards.menu import main_menu_keyboard
from bot.keyboards.settings import (
    BTN_SAVE,
    is_settings_button,
    profile_from_button,
    settings_keyboard,
    settings_menu_keyboard,
)
from bot.storage.models import utc_now_iso
from bot.storage.user_prefs import delivery_prefs_from_profile
from bot.utils.access import deny_if_not_allowed

IN_SETTINGS_KEY = "in_settings"
KEYBOARD_MSG_KEY = "keyboard_msg_id"
# Telegram rejects zero-width/blank text; this character is invisible in clients.
_KEYBOARD_ONLY = "\u3164"


async def _set_reply_keyboard(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    keyboard,
) -> None:
    bot = message.get_bot()
    chat_id = message.chat_id
    sent = await bot.send_message(
        chat_id=chat_id,
        text=_KEYBOARD_ONLY,
        reply_markup=keyboard,
        disable_notification=True,
    )
    old_id = context.user_data.pop(KEYBOARD_MSG_KEY, None)
    context.user_data[KEYBOARD_MSG_KEY] = sent.message_id
    if old_id and old_id != sent.message_id:
        with contextlib.suppress(Exception):
            await bot.delete_message(chat_id=chat_id, message_id=old_id)


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await deny_if_not_allowed(update, context):
        return
    await _send_settings(update, context)


async def _send_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    db = context.bot_data["db"]
    now = utc_now_iso()
    await db.touch_user(
        user.id,
        username=user.username,
        first_name=user.first_name,
        now=now,
    )
    profile = await db.get_user_audio_profile(user.id)
    context.user_data[IN_SETTINGS_KEY] = True
    await _set_reply_keyboard(message, context, settings_menu_keyboard(profile))


async def settings_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    message = update.effective_message
    if not message or not message.text:
        return False

    text = message.text.strip()
    if not is_settings_button(text):
        return False

    if await deny_if_not_allowed(update, context):
        return True

    if text == BTN_SAVE:
        await _exit_settings(update, context)
        return True

    profile = profile_from_button(text)
    if not profile:
        return False

    user = update.effective_user
    if not user:
        return True

    db = context.bot_data["db"]
    now = utc_now_iso()
    await db.set_user_audio_profile(user.id, profile, now=now)
    context.user_data[IN_SETTINGS_KEY] = True
    await _set_reply_keyboard(message, context, settings_menu_keyboard(profile))
    return True


async def _exit_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    context.user_data.pop(IN_SETTINGS_KEY, None)
    await _set_reply_keyboard(message, context, main_menu_keyboard())


async def pref_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, profile: str) -> None:
    if await deny_if_not_allowed(update, context):
        return

    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return

    db = context.bot_data["db"]
    now = utc_now_iso()
    await db.set_user_audio_profile(user.id, profile, now=now)
    prefs = delivery_prefs_from_profile(profile)

    try:
        await query.edit_message_reply_markup(reply_markup=settings_keyboard(prefs.profile))
    except Exception:
        pass
