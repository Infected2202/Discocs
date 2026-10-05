"""Доставка трека «через карточку загрузки»: карточка с обложкой и «⏳ Готовлю...»
заменяется на аудио, а при ошибке — на понятный текст.

Общая для кнопки «Получить» (handlers/callbacks.py) и отправки из веба
(services/internal_api.py), чтобы обе дороги вели себя одинаково.
"""
import logging

from telegram import Bot, Message
from telegram.error import BadRequest

from bot.services.delivery import DeliveryService
from bot.services.navidrome import NavidromeError
from bot.services.transcoder import TranscodeError

logger = logging.getLogger(__name__)

LOADING_CAPTION = "⏳ Готовлю..."
ERROR_DETAIL_LIMIT = 600
NAVIDROME_UNAVAILABLE = "Navidrome сейчас недоступен."


async def safe_edit_loading_message(message: Message, text: str) -> None:
    try:
        if message.photo:
            await message.edit_caption(caption=text[:ERROR_DETAIL_LIMIT])
        else:
            await message.edit_text(text=text[:ERROR_DETAIL_LIMIT])
    except BadRequest:
        logger.debug("Could not edit loading message %s", message.message_id)


async def deliver_into_loading_card(
    bot: Bot,
    delivery: DeliveryService,
    loading: Message,
    song_id: str,
    *,
    user_id: int | None,
) -> None:
    try:
        await delivery.deliver_track_to_message(
            bot,
            chat_id=loading.chat_id,
            message_id=loading.message_id,
            song_id=song_id,
            user_id=user_id,
        )
    except NavidromeError:
        await safe_edit_loading_message(loading, NAVIDROME_UNAVAILABLE)
    except TranscodeError as exc:
        if str(exc) == "file_too_large":
            await safe_edit_loading_message(
                loading,
                "Файл слишком большой для отправки в Telegram.",
            )
        elif str(exc) in ("telegram_replace_failed", "telegram_upload_failed"):
            await safe_edit_loading_message(loading, "Не удалось заменить сообщение загрузки на аудио.")
        else:
            logger.exception("deliver_track_to_message failed: %s", exc)
            await safe_edit_loading_message(loading, "Не удалось подготовить аудио.")
    except Exception:
        logger.exception("deliver_track_to_message failed for song_id=%s", song_id)
        await safe_edit_loading_message(loading, "Не удалось получить трек.")
