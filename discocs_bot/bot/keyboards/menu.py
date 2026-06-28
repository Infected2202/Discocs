from telegram import BotCommand, KeyboardButton, ReplyKeyboardMarkup

SEARCH_HINT = "Артист, альбом или трек..."
BTN_RANDOM = "🎲 Случайный"
BTN_RADIO_LAST = "📻 Радио с последнего"
BTN_SETTINGS = "⚙️ Настройки"
BTN_HELP = "ℹ️ Справка"

MENU_BUTTONS = {BTN_RANDOM, BTN_RADIO_LAST, BTN_SETTINGS, BTN_HELP}

BOT_COMMANDS = [
    BotCommand("menu", "Показать меню"),
    BotCommand("settings", "Качество (MP3/Opus/FLAC)"),
    BotCommand("random", "Случайный трек"),
    BotCommand("help", "Справка"),
]


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_RANDOM), KeyboardButton(BTN_RADIO_LAST)],
            [KeyboardButton(BTN_SETTINGS), KeyboardButton(BTN_HELP)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=SEARCH_HINT,
    )
