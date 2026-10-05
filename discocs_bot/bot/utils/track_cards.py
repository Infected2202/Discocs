import asyncio
import logging
from pathlib import Path

from telegram import Bot, InputFile, InputMediaPhoto, Message
from telegram.error import BadRequest, NetworkError, TimedOut

from bot.keyboards.track import album_card_keyboard, track_keyboard
from bot.services.navidrome import NavidromeClient
from bot.storage.models import Album, Track
from bot.utils.cover import download_track_cover, placeholder_cover_path

logger = logging.getLogger(__name__)

EDIT_RETRY_ATTEMPTS = 3
EDIT_RETRY_DELAY_SEC = 0.4
COVER_FILENAME = "cover.jpg"


def format_track_card(track: Track) -> str:
    lines = [f"{track.artist} — {track.title}", f"Альбом: {track.album}"]
    if track.year:
        lines.append(f"Год: {track.year}")
    return "\n".join(lines)


def track_card_caption(
    track: Track,
    *,
    index: int | None = None,
    caption_prefix: str | None = None,
) -> str:
    caption = format_track_card(track)
    if index is not None:
        caption = f"#{index}\n{caption}"
    if caption_prefix:
        return f"{caption_prefix}\n\n{caption}"
    return caption


async def send_track_card(
    bot: Bot,
    chat_id: int,
    track: Track,
    *,
    navidrome: NavidromeClient,
    temp_dir: Path,
    index: int | None = None,
    keyboard=None,
    caption_prefix: str | None = None,
) -> Message:
    caption = track_card_caption(track, index=index, caption_prefix=caption_prefix)
    keyboard = keyboard or track_keyboard(track.id, track.album_id)
    cover_path = await download_track_cover(track, navidrome, temp_dir)
    own_cover = cover_path.name.startswith("card_")

    try:
        with cover_path.open("rb") as cover_file:
            return await bot.send_photo(
                chat_id=chat_id,
                photo=InputFile(cover_file, filename=COVER_FILENAME),
                caption=caption,
                reply_markup=keyboard,
            )
    finally:
        if own_cover:
            cover_path.unlink(missing_ok=True)


async def send_track_loading_card(
    bot: Bot,
    chat_id: int,
    track: Track,
    *,
    navidrome: NavidromeClient,
    temp_dir: Path,
    caption: str,
) -> Message:
    cover_path = await download_track_cover(track, navidrome, temp_dir)
    own_cover = cover_path.name.startswith("card_")

    try:
        with cover_path.open("rb") as cover_file:
            return await bot.send_photo(
                chat_id=chat_id,
                photo=InputFile(cover_file, filename=COVER_FILENAME),
                caption=caption,
            )
    finally:
        if own_cover:
            cover_path.unlink(missing_ok=True)


async def edit_track_card(
    bot: Bot,
    chat_id: int,
    message_id: int,
    track: Track,
    *,
    navidrome: NavidromeClient,
    temp_dir: Path,
    index: int | None = None,
    keyboard=None,
    caption_prefix: str | None = None,
) -> bool:
    caption = track_card_caption(track, index=index, caption_prefix=caption_prefix)
    keyboard = keyboard or track_keyboard(track.id, track.album_id)
    cover_path = await download_track_cover(track, navidrome, temp_dir)
    own_cover = cover_path.name.startswith("card_")
    last_error: Exception | None = None

    try:
        for attempt in range(1, EDIT_RETRY_ATTEMPTS + 1):
            try:
                with cover_path.open("rb") as cover_file:
                    await bot.edit_message_media(
                        chat_id=chat_id,
                        message_id=message_id,
                        media=InputMediaPhoto(
                            media=InputFile(
                                cover_file,
                                filename=COVER_FILENAME,
                                attach=True,
                            ),
                            caption=caption,
                        ),
                        reply_markup=keyboard,
                    )
                return True
            except BadRequest as exc:
                last_error = exc
                if "message is not modified" in str(exc).lower():
                    return True
                logger.warning(
                    "edit_track_card failed for message %s track %s (attempt %s/%s): %s",
                    message_id,
                    track.id,
                    attempt,
                    EDIT_RETRY_ATTEMPTS,
                    exc,
                )
            except (NetworkError, TimedOut) as exc:
                last_error = exc
                logger.warning(
                    "edit_track_card network error for message %s track %s (attempt %s/%s): %s",
                    message_id,
                    track.id,
                    attempt,
                    EDIT_RETRY_ATTEMPTS,
                    exc,
                )

            if attempt < EDIT_RETRY_ATTEMPTS:
                await asyncio.sleep(EDIT_RETRY_DELAY_SEC)

        logger.error(
            "edit_track_card gave up for message %s track %s: %s",
            message_id,
            track.id,
            last_error,
        )
        return False
    finally:
        if own_cover:
            cover_path.unlink(missing_ok=True)


async def reply_track_card(
    message: Message,
    track: Track,
    *,
    navidrome: NavidromeClient,
    temp_dir: Path,
    index: int | None = None,
    keyboard=None,
    caption_prefix: str | None = None,
) -> Message:
    return await send_track_card(
        message.get_bot(),
        message.chat_id,
        track,
        navidrome=navidrome,
        temp_dir=temp_dir,
        index=index,
        keyboard=keyboard,
        caption_prefix=caption_prefix,
    )


def album_card_caption(album: Album) -> str:
    lines = [f"📀 {album.artist} — {album.title}"]
    if album.year:
        lines.append(f"Год: {album.year}")
    if album.track_count:
        lines.append(f"Треков: {album.track_count}")
    return "\n".join(lines)


async def send_album_card(
    bot: Bot,
    chat_id: int,
    album: Album,
    *,
    navidrome: NavidromeClient,
    temp_dir: Path,
    open_url: str | None = None,
) -> Message:
    """Карточка релиза: обложка, подпись и кнопки «Отправить все треки» / «Открыть».

    Telegram отказывает URL-кнопкам с адресами, которые считает невалидными
    (localhost, голый IP без DISCOCS_PUBLIC_URL) — тогда карточка уходит без
    «Открыть», а не не уходит вовсе.
    """
    cover_path = placeholder_cover_path()
    own_cover = False
    if album.cover_art_id:
        candidate = temp_dir / f"card_album_{album.id}.cover.jpg"
        if await navidrome.download_cover_art(album.cover_art_id, candidate, size=None):
            cover_path, own_cover = candidate, True

    async def send(keyboard) -> Message:
        with cover_path.open("rb") as cover_file:
            return await bot.send_photo(
                chat_id=chat_id,
                photo=InputFile(cover_file, filename=COVER_FILENAME),
                caption=album_card_caption(album),
                reply_markup=keyboard,
            )

    try:
        try:
            return await send(album_card_keyboard(album.id, open_url))
        except BadRequest as exc:
            if not open_url or "url" not in str(exc).lower():
                raise
            logger.warning("Telegram rejected open_url %r: %s", open_url, exc)
            return await send(album_card_keyboard(album.id))
    finally:
        if own_cover:
            cover_path.unlink(missing_ok=True)
