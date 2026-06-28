import asyncio
import contextlib
import logging

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters
from telegram.request import HTTPXRequest

import bot.utils.logging  # noqa: F401
from bot.config import get_settings
from bot.handlers.callbacks import callback_handler
from bot.handlers.menu import menu_command, menu_message_handler
from bot.handlers.random import random_command
from bot.handlers.search import audio_message_handler, search_command
from bot.handlers.settings import settings_command
from bot.handlers.start import help_command, start_command
from bot.keyboards.menu import BOT_COMMANDS
from bot.services.delivery import DeliveryService
from bot.services.discocs import DiscocsClient
from bot.services.navidrome import NavidromeClient
from bot.services.transcoder import Transcoder
from bot.storage.db import Database
from bot.utils.single_instance import acquire, release

logger = logging.getLogger(__name__)


async def _post_init(application: Application) -> None:
    settings = application.bot_data["settings"]
    db: Database = application.bot_data["db"]
    navidrome: NavidromeClient = application.bot_data["navidrome"]
    discocs: DiscocsClient = application.bot_data["discocs"]

    await db.connect()
    settings.temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        await navidrome.ping()
    except Exception:
        logger.warning("Navidrome ping failed on startup")

    try:
        await discocs.ping()
    except Exception:
        logger.warning("Discocs ping failed on startup")

    await application.bot.set_my_commands(BOT_COMMANDS)


async def _post_shutdown(application: Application) -> None:
    db: Database = application.bot_data["db"]
    navidrome: NavidromeClient = application.bot_data["navidrome"]
    discocs: DiscocsClient = application.bot_data["discocs"]
    await db.close()
    await navidrome.close()
    await discocs.close()


def _telegram_request() -> HTTPXRequest:
    return HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=60.0,
        write_timeout=60.0,
        pool_timeout=30.0,
        media_write_timeout=300.0,
    )


def build_application() -> Application:
    settings = get_settings()
    db = Database(settings)
    navidrome = NavidromeClient(settings)
    discocs = DiscocsClient(settings, navidrome)
    transcoder = Transcoder(settings)
    delivery = DeliveryService(settings, navidrome, transcoder, db)

    application = (
        Application.builder()
        .token(settings.bot_token)
        .request(_telegram_request())
        .get_updates_request(_telegram_request())
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    application.bot_data["settings"] = settings
    application.bot_data["db"] = db
    application.bot_data["navidrome"] = navidrome
    application.bot_data["discocs"] = discocs
    application.bot_data["transcoder"] = transcoder
    application.bot_data["delivery"] = delivery

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("random", random_command))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_message_handler))
    application.add_handler(MessageHandler(filters.AUDIO, audio_message_handler))

    return application


async def _shutdown(application: Application) -> None:
    logger.info("Shutting down...")
    with contextlib.suppress(Exception, asyncio.TimeoutError):
        await asyncio.wait_for(application.updater.stop(), timeout=5)
    with contextlib.suppress(Exception):
        await application.stop()
    with contextlib.suppress(Exception):
        await _post_shutdown(application)
    with contextlib.suppress(Exception):
        await application.shutdown()


async def _run_bot() -> None:
    settings = get_settings()
    acquire(settings.sqlite_path.parent)
    application = build_application()
    await application.initialize()
    await _post_init(application)
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    logger.info("Bot is running. Ctrl+C to stop (or stop.bat if it hangs).")
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        await _shutdown(application)
        release(settings.sqlite_path.parent)


def main() -> None:
    settings = get_settings()
    if not settings.bot_token.strip() or settings.bot_token == "placeholder":
        logger.error("Set a real BOT_TOKEN from @BotFather in .env")
        raise SystemExit(1)

    logger.info("Starting Discocs Bot")
    try:
        asyncio.run(_run_bot())
    except KeyboardInterrupt:
        logger.info("Stopped.")


if __name__ == "__main__":
    main()
