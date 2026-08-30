import logging
from dataclasses import dataclass, replace
from pathlib import Path

from telegram import Bot, Message
from telegram.error import BadRequest

from bot.keyboards.pagination import carousel_keyboard
from bot.services.navidrome import NavidromeClient, NavidromeError
from bot.storage.models import Track
from bot.utils.track_cards import edit_track_card, send_track_card

logger = logging.getLogger(__name__)

SEARCH_QUERY_KEY = "search_query"
RADIO_SEED_KEY = "radio_seed"
RADIO_TITLE_KEY = "radio_title"
LAST_TRACK_SEED_KEY = "last_track_seed"
LAST_TRACK_TITLE_KEY = "last_track_title"
RESULTS_VIEW_KEY = "results_view"
RESULTS_HISTORY_KEY = "results_history"
RESULTS_HISTORY_LIMIT = 5


@dataclass(slots=True)
class ResultsView:
    kind: str
    session_key: str
    chat_id: int
    message_id: int
    header: str
    tracks: list[Track]
    has_next: bool
    page_size: int
    slot: int = 0


def position_label(view: ResultsView) -> str:
    current = view.slot + 1
    total = len(view.tracks)
    if view.has_next:
        return f"{current}/{total}+"
    return f"{current}/{total}"


def remember_last_track(context, *, song_id: str, title: str) -> None:
    context.user_data[LAST_TRACK_SEED_KEY] = song_id
    context.user_data[LAST_TRACK_TITLE_KEY] = title


def get_results_view(context) -> ResultsView | None:
    raw = context.user_data.get(RESULTS_VIEW_KEY)
    if not isinstance(raw, ResultsView):
        return None
    return raw


def set_results_view(context, view: ResultsView) -> None:
    context.user_data[RESULTS_VIEW_KEY] = view


def get_results_history(context) -> list[ResultsView]:
    raw = context.user_data.get(RESULTS_HISTORY_KEY)
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, ResultsView)]


def set_results_history(context, history: list[ResultsView]) -> None:
    context.user_data[RESULTS_HISTORY_KEY] = history[-RESULTS_HISTORY_LIMIT:]


def push_results_history(context, view: ResultsView) -> None:
    snapshot = replace(view, tracks=list(view.tracks))
    history = get_results_history(context)
    history.append(snapshot)
    set_results_history(context, history)


def pop_results_history(context) -> ResultsView | None:
    history = get_results_history(context)
    if not history:
        return None
    view = history.pop()
    set_results_history(context, history)
    return view


def detach_card_from_view(context, message_id: int) -> None:
    view = get_results_view(context)
    if not view:
        return
    if view.message_id == message_id:
        context.user_data.pop(RESULTS_VIEW_KEY, None)


def forget_results_view(context) -> None:
    """Unbind the carousel without deleting its message.

    Results update in place, which reads as an answer only while that message
    is the last one in the chat. Once the bot has sent something else — a link
    card, an audio file — updating it silently edits a message that has scrolled
    away, and the next search looks like no answer at all.
    """
    context.user_data.pop(RESULTS_VIEW_KEY, None)
    context.user_data.pop(RESULTS_HISTORY_KEY, None)


async def clear_results_view(bot: Bot, context) -> None:
    view = get_results_view(context)
    if not view:
        return
    try:
        await bot.delete_message(view.chat_id, view.message_id)
    except BadRequest:
        logger.debug("Could not delete results message %s", view.message_id)
    context.user_data.pop(RESULTS_VIEW_KEY, None)
    context.user_data.pop(RESULTS_HISTORY_KEY, None)


def _track_index(view: ResultsView) -> int:
    return view.slot + 1


def _track_keyboard(context, view: ResultsView):
    track = view.tracks[view.slot]
    return carousel_keyboard(
        song_id=track.id,
        album_id=track.album_id,
        slot=view.slot,
        count=len(view.tracks),
        has_next=view.has_next,
        position=position_label(view),
        has_back=bool(get_results_history(context)),
    )


async def fetch_more_tracks(context, view: ResultsView) -> bool:
    if not view.has_next:
        return False

    offset = len(view.tracks)
    if view.kind == "search":
        navidrome = context.bot_data["navidrome"]
        try:
            tracks, has_next = await navidrome.search_tracks(
                view.session_key,
                limit=view.page_size,
                offset=offset,
            )
        except NavidromeError:
            logger.exception("Failed to fetch more search results")
            return False
    elif view.kind == "radio":
        discocs = context.bot_data["discocs"]
        try:
            tracks, has_next = await discocs.get_similar_tracks(
                view.session_key,
                offset=offset,
                limit=view.page_size,
            )
        except Exception:
            logger.exception("Failed to fetch more radio results")
            return False
    else:
        return False

    if not tracks:
        view.has_next = False
        return False

    view.tracks.extend(tracks)
    view.has_next = has_next
    set_results_view(context, view)
    return True


async def move_results_slot(
    context,
    bot: Bot,
    *,
    target_slot: int,
    navidrome: NavidromeClient,
    temp_dir: Path,
) -> None:
    view = get_results_view(context)
    if not view or not view.tracks or target_slot < 0:
        return

    while target_slot >= len(view.tracks) and view.has_next:
        if not await fetch_more_tracks(context, view):
            break

    if target_slot >= len(view.tracks):
        return

    await show_carousel_slot(
        context,
        bot,
        slot=target_slot,
        navidrome=navidrome,
        temp_dir=temp_dir,
    )


async def show_carousel_slot(
    context,
    bot: Bot,
    *,
    slot: int,
    navidrome: NavidromeClient,
    temp_dir: Path,
) -> None:
    view = get_results_view(context)
    if not view or not view.tracks:
        return
    view.slot = max(0, min(slot, len(view.tracks) - 1))
    track = view.tracks[view.slot]
    keyboard = _track_keyboard(context, view)
    card_kwargs = {
        "index": _track_index(view),
        "keyboard": keyboard,
        "caption_prefix": view.header,
    }
    if await edit_track_card(
        bot,
        view.chat_id,
        view.message_id,
        track,
        navidrome=navidrome,
        temp_dir=temp_dir,
        **card_kwargs,
    ):
        set_results_view(context, view)
        return

    logger.warning(
        "Carousel fallback: replacing message %s with new card for track %s slot %s",
        view.message_id,
        track.id,
        view.slot,
    )
    card_msg = await send_track_card(
        bot,
        view.chat_id,
        track,
        navidrome=navidrome,
        temp_dir=temp_dir,
        **card_kwargs,
    )
    try:
        await bot.delete_message(view.chat_id, view.message_id)
    except BadRequest as exc:
        logger.warning(
            "Could not delete stale carousel message %s after fallback: %s",
            view.message_id,
            exc,
        )
    view.message_id = card_msg.message_id
    set_results_view(context, view)


async def show_or_update_track_results(
    context,
    bot: Bot,
    *,
    chat_id: int,
    anchor: Message | None,
    tracks: list[Track],
    navidrome: NavidromeClient,
    temp_dir: Path,
    header: str,
    page_size: int,
    page_kind: str,
    session_key: str,
    has_next: bool,
) -> None:
    _ = anchor
    view = get_results_view(context)
    new_session = (
        view is None
        or view.kind != page_kind
        or view.session_key != session_key
        or view.chat_id != chat_id
    )

    if new_session:
        if view and view.chat_id == chat_id:
            push_results_history(context, view)
            view.header = header
            view.tracks = tracks
            view.has_next = has_next
            view.page_size = page_size
            view.slot = 0
            view.kind = page_kind
            view.session_key = session_key
            set_results_view(context, view)
            await show_carousel_slot(
                context,
                bot,
                slot=0,
                navidrome=navidrome,
                temp_dir=temp_dir,
            )
            return

        if view:
            await clear_results_view(bot, context)

        view = ResultsView(
            kind=page_kind,
            session_key=session_key,
            chat_id=chat_id,
            message_id=0,
            header=header,
            tracks=tracks,
            has_next=has_next,
            page_size=page_size,
            slot=0,
        )
        track = tracks[0]
        card_msg = await send_track_card(
            bot,
            chat_id,
            track,
            navidrome=navidrome,
            temp_dir=temp_dir,
            index=_track_index(view),
            keyboard=_track_keyboard(context, view),
            caption_prefix=view.header,
        )
        view.message_id = card_msg.message_id
        set_results_view(context, view)
        return

    view.tracks = tracks
    view.has_next = has_next
    view.page_size = page_size
    view.header = header
    view.slot = 0
    await show_carousel_slot(
        context,
        bot,
        slot=0,
        navidrome=navidrome,
        temp_dir=temp_dir,
    )


async def restore_previous_results_view(
    context,
    bot: Bot,
    *,
    navidrome: NavidromeClient,
    temp_dir: Path,
) -> bool:
    current = get_results_view(context)
    previous = pop_results_history(context)
    if not current or not previous:
        return False
    previous.message_id = current.message_id
    set_results_view(context, previous)
    await show_carousel_slot(
        context,
        bot,
        slot=previous.slot,
        navidrome=navidrome,
        temp_dir=temp_dir,
    )
    return True


async def send_track_results_page(
    message: Message,
    tracks: list[Track],
    *,
    context,
    navidrome: NavidromeClient,
    temp_dir: Path,
    header: str,
    page_size: int,
    page_kind: str,
    session_key: str,
    has_next: bool,
) -> None:
    await show_or_update_track_results(
        context,
        message.get_bot(),
        chat_id=message.chat_id,
        anchor=message,
        tracks=tracks,
        navidrome=navidrome,
        temp_dir=temp_dir,
        header=header,
        page_size=page_size,
        page_kind=page_kind,
        session_key=session_key,
        has_next=has_next,
    )
