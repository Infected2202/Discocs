from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def external_link_keyboard(media_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🎵 Скачать MP3", callback_data=f"extget:{media_key}")]]
    )
