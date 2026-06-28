from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards.menu import main_menu_keyboard
from bot.utils.access import deny_if_not_allowed

HELP_TEXT = """Discocs Bot — доступ к музыкальной библиотеке.

Поиск — просто напиши артиста, альбом или трек
🎲 Случайный — случайный трек из библиотеки
📻 Радио с последнего — похожие на последний трек
⚙️ Настройки — MP3, Opus или FLAC

Команды: /menu, /settings, /random, /help

На карточке трека:
📥 Получить · 📻 Радио · 📀 Альбом"""


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await deny_if_not_allowed(update, context):
        return
    await update.effective_message.reply_text(HELP_TEXT, reply_markup=main_menu_keyboard())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start_command(update, context)
