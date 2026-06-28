from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def carousel_keyboard(
    *,
    song_id: str,
    album_id: str | None,
    slot: int,
    count: int,
    has_next: bool,
    position: str,
    has_back: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    nav_row: list[InlineKeyboardButton] = []
    if slot > 0:
        nav_row.append(InlineKeyboardButton("⬅️", callback_data=f"result_move:{slot - 1}"))
    nav_row.append(InlineKeyboardButton(position, callback_data="noop:0"))
    if slot + 1 < count or has_next:
        nav_row.append(InlineKeyboardButton("➡️", callback_data=f"result_move:{slot + 1}"))
    rows.append(nav_row)

    rows.append(
        [
            InlineKeyboardButton("📥 Получить", callback_data=f"get:{song_id}"),
            InlineKeyboardButton("📻 Радио", callback_data=f"radio:{song_id}"),
        ]
    )

    if album_id:
        rows.append([InlineKeyboardButton("📀 Получить альбом", callback_data=f"album:{album_id}")])
    if has_back:
        rows.append([InlineKeyboardButton("↩️ Назад", callback_data="result_back:0")])

    return InlineKeyboardMarkup(rows)
