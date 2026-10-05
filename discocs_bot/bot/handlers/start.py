import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards.menu import main_menu_keyboard
from bot.services.discocs import DiscocsClient, DiscocsError
from bot.utils.access import deny_if_not_allowed

logger = logging.getLogger(__name__)

HELP_TEXT = """Discocs Bot — доступ к музыкальной библиотеке.

Поиск — просто напиши артиста, альбом или трек
🔗 Ссылка (YouTube, SoundCloud, Bandcamp…) — «Скачать MP3» или «Радио» по звуку
🎧 Аудиофайл в чат — поиск по тегам или радио по звуку
🎲 Случайный — случайный трек из библиотеки
📻 Радио с последнего — похожие на последний трек
⚙️ Настройки — MP3, Opus или FLAC

Команды: /menu, /settings, /random, /help

На карточке трека:
📥 Получить · 📻 Радио · 📀 Альбом"""

# Payload deep link'а t.me/<bot>?start=link_<token> — см. docs/telegram.md.
LINK_PAYLOAD_PREFIX = "link_"
LINK_OK = "Telegram привязан к аккаунту discocs «{username}». Теперь из «Поделиться» можно отправлять музыку сюда."
LINK_INVALID = "Ссылка для привязки устарела или уже использована. Открой настройки discocs и нажми «Подключить» ещё раз."
LINK_FAILED = "Не получилось связаться с discocs. Попробуй ещё раз чуть позже."


async def _redeem_link(update: Update, context: ContextTypes.DEFAULT_TYPE, token: str) -> None:
    discocs: DiscocsClient = context.bot_data["discocs"]
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    try:
        username = await discocs.redeem_link(
            token,
            telegram_user_id=user.id,
            telegram_username=user.username,
        )
    except DiscocsError:
        logger.warning("Telegram link redeem failed for user_id=%s", user.id, exc_info=True)
        await message.reply_text(LINK_FAILED)
        return
    if username is None:
        await message.reply_text(LINK_INVALID)
        return
    logger.info("Telegram user_id=%s linked to discocs user %s", user.id, username)
    await message.reply_text(LINK_OK.format(username=username), reply_markup=main_menu_keyboard())


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await deny_if_not_allowed(update, context):
        return
    payload = context.args[0] if context.args else ""
    if payload.startswith(LINK_PAYLOAD_PREFIX):
        await _redeem_link(update, context, payload[len(LINK_PAYLOAD_PREFIX):])
        return
    await update.effective_message.reply_text(HELP_TEXT, reply_markup=main_menu_keyboard())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await deny_if_not_allowed(update, context):
        return
    await update.effective_message.reply_text(HELP_TEXT, reply_markup=main_menu_keyboard())
