from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def external_link_keyboard(media_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎵 Скачать MP3", callback_data=f"extget:{media_key}"),
                InlineKeyboardButton("📻 Радио", callback_data=f"extradio:{media_key}"),
            ]
        ]
    )


def external_audio_keyboard(media_key: str, *, can_analyze: bool) -> InlineKeyboardMarkup:
    """Buttons under an audio file someone sent to the chat.

    Radio needs the bytes, and Telegram refuses to hand a bot anything over
    20 MB — for those, tag search is all that is left.
    """
    rows = [[InlineKeyboardButton("🔎 Поиск по тегам", callback_data=f"extsearch:{media_key}")]]
    if can_analyze:
        rows[0].append(InlineKeyboardButton("📻 Радио по звуку", callback_data=f"extradio:{media_key}"))
    return InlineKeyboardMarkup(rows)


def library_match_keyboard(song_id: str, media_key: str, album_id: str | None = None) -> InlineKeyboardMarkup:
    """A linked track we already own — plus a way out if the match is wrong."""
    rows = [
        [
            InlineKeyboardButton("📥 Получить", callback_data=f"get:{song_id}"),
            InlineKeyboardButton("📻 Радио", callback_data=f"radio:{song_id}"),
        ]
    ]
    if album_id:
        rows.append([InlineKeyboardButton("📀 Получить альбом", callback_data=f"album:{album_id}")])
    rows.append(
        [InlineKeyboardButton("🔗 Всё равно скачать по ссылке", callback_data=f"extget:{media_key}")]
    )
    return InlineKeyboardMarkup(rows)
