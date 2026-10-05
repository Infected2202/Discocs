from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def track_keyboard(song_id: str, album_id: str | None = None) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("📥 Получить", callback_data=f"get:{song_id}"),
            InlineKeyboardButton("📻 Радио", callback_data=f"radio:{song_id}"),
        ],
    ]
    if album_id:
        rows.append([InlineKeyboardButton("📀 Получить альбом", callback_data=f"album:{album_id}")])
    return InlineKeyboardMarkup(rows)


def album_card_keyboard(album_id: str, open_url: str | None = None) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("📀 Отправить все треки", callback_data=f"album:{album_id}")]]
    if open_url:
        rows.append([InlineKeyboardButton("🌐 Открыть в discocs", url=open_url)])
    return InlineKeyboardMarkup(rows)
