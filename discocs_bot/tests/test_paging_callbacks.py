from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.pop("bot", None)

from bot.handlers import callbacks as callbacks_module
from bot.handlers import search as search_module
from bot.utils import track_pages as track_pages_module


def _context(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        bot=SimpleNamespace(name="bot"),
        bot_data={
            "settings": SimpleNamespace(temp_dir=tmp_path / "temp"),
            "navidrome": SimpleNamespace(name="navidrome"),
        },
    )


def test_search_page_callback_delegates_to_track_pages(monkeypatch, tmp_path: Path) -> None:
    context = _context(tmp_path)
    calls: list[tuple[object, object, int, object, Path]] = []

    async def fake_move_results_slot(ctx, bot, *, target_slot: int, navidrome, temp_dir: Path) -> None:
        calls.append((ctx, bot, target_slot, navidrome, temp_dir))

    monkeypatch.setattr(track_pages_module, "move_results_slot", fake_move_results_slot)

    asyncio.run(search_module.search_page_callback(None, context, 3))

    assert context.bot_data["settings"].temp_dir.exists()
    assert calls == [
        (
            context,
            context.bot,
            3,
            context.bot_data["navidrome"],
            context.bot_data["settings"].temp_dir,
        )
    ]


def test_result_callbacks_delegate_with_expected_slot(monkeypatch, tmp_path: Path) -> None:
    context = _context(tmp_path)
    move_calls: list[tuple[object, object, int, object, Path]] = []
    back_calls: list[tuple[object, object, object, Path]] = []

    async def fake_move_results_slot(ctx, bot, *, target_slot: int, navidrome, temp_dir: Path) -> None:
        move_calls.append((ctx, bot, target_slot, navidrome, temp_dir))

    async def fake_restore_previous_results_view(ctx, bot, *, navidrome, temp_dir: Path) -> None:
        back_calls.append((ctx, bot, navidrome, temp_dir))

    monkeypatch.setattr(callbacks_module, "move_results_slot", fake_move_results_slot)
    monkeypatch.setattr(callbacks_module, "restore_previous_results_view", fake_restore_previous_results_view)

    asyncio.run(callbacks_module.result_move_callback(None, context, 5))
    asyncio.run(callbacks_module.radio_page_callback(None, context, 7))
    asyncio.run(callbacks_module.result_back_callback(None, context))

    assert context.bot_data["settings"].temp_dir.exists()
    assert move_calls == [
        (
            context,
            context.bot,
            5,
            context.bot_data["navidrome"],
            context.bot_data["settings"].temp_dir,
        ),
        (
            context,
            context.bot,
            7,
            context.bot_data["navidrome"],
            context.bot_data["settings"].temp_dir,
        ),
    ]
    assert back_calls == [
        (
            context,
            context.bot,
            context.bot_data["navidrome"],
            context.bot_data["settings"].temp_dir,
        )
    ]


def test_show_or_update_track_results_creates_new_session_card(monkeypatch, tmp_path: Path) -> None:
    context = SimpleNamespace(user_data={}, bot_data={})
    bot = SimpleNamespace()
    track = SimpleNamespace(id="track-1", album_id="album-1")
    sent: list[tuple[object, int, object, object, Path, int, object, str]] = []

    async def fake_send_track_card(
        passed_bot,
        chat_id: int,
        passed_track,
        *,
        navidrome,
        temp_dir: Path,
        index: int,
        keyboard,
        caption_prefix: str,
    ) -> SimpleNamespace:
        sent.append((passed_bot, chat_id, passed_track, navidrome, temp_dir, index, keyboard, caption_prefix))
        return SimpleNamespace(message_id=42)

    monkeypatch.setattr(track_pages_module, "carousel_keyboard", lambda **kwargs: kwargs)
    monkeypatch.setattr(track_pages_module, "send_track_card", fake_send_track_card)

    asyncio.run(
        track_pages_module.show_or_update_track_results(
            context,
            bot,
            chat_id=99,
            anchor=None,
            tracks=[track],
            navidrome=SimpleNamespace(),
            temp_dir=tmp_path / "temp",
            header="Results",
            page_size=10,
            page_kind="search",
            session_key="query",
            has_next=False,
        )
    )

    view = track_pages_module.get_results_view(context)
    assert sent == [(bot, 99, track, sent[0][3], tmp_path / "temp", 1, sent[0][6], "Results")]
    assert view is not None
    assert view.chat_id == 99
    assert view.message_id == 42
    assert view.session_key == "query"


def _results_view(**overrides) -> track_pages_module.ResultsView:
    defaults = dict(
        kind="search",
        session_key="старый запрос",
        chat_id=99,
        message_id=111,
        header="Поиск: «старый запрос»",
        tracks=[SimpleNamespace(id="old-track", album_id="old-album")],
        has_next=False,
        page_size=10,
        slot=0,
    )
    defaults.update(overrides)
    return track_pages_module.ResultsView(**defaults)


def test_new_search_sends_a_new_card_instead_of_editing_the_old_one(monkeypatch, tmp_path: Path) -> None:
    """Карточка читается как ответ, только пока она последняя в чате.

    Стоит боту прислать что-то ещё (трек, альбом, радио), и правка на месте
    молча меняет уехавшее вверх сообщение — для пользователя это выглядит как
    «поиск не отвечает».
    """
    context = SimpleNamespace(user_data={}, bot_data={})
    bot = SimpleNamespace()
    new_track = SimpleNamespace(id="new-track", album_id="new-album")
    track_pages_module.set_results_view(context, _results_view())

    sent: list[tuple[int, object]] = []
    edited: list[int] = []
    cleared: list[bool] = []

    async def fake_send_track_card(passed_bot, chat_id: int, passed_track, **kwargs) -> SimpleNamespace:
        sent.append((chat_id, passed_track))
        return SimpleNamespace(message_id=222)

    async def fake_show_carousel_slot(ctx, passed_bot, *, slot: int, navidrome, temp_dir: Path) -> None:
        edited.append(slot)

    async def fake_clear_results_view(passed_bot, ctx) -> None:
        cleared.append(True)

    monkeypatch.setattr(track_pages_module, "carousel_keyboard", lambda **kwargs: kwargs)
    monkeypatch.setattr(track_pages_module, "send_track_card", fake_send_track_card)
    monkeypatch.setattr(track_pages_module, "show_carousel_slot", fake_show_carousel_slot)
    monkeypatch.setattr(track_pages_module, "clear_results_view", fake_clear_results_view)

    asyncio.run(
        track_pages_module.show_or_update_track_results(
            context,
            bot,
            chat_id=99,
            anchor=None,
            tracks=[new_track],
            navidrome=SimpleNamespace(),
            temp_dir=tmp_path / "temp",
            header="Поиск: «новый запрос»",
            page_size=10,
            page_kind="search",
            session_key="новый запрос",
            has_next=False,
        )
    )

    assert sent == [(99, new_track)], "новая выдача должна приходить новым сообщением"
    assert edited == [], "старую карточку править нельзя — её уже не видно"
    assert cleared == [], "и удалять её тоже не просили"

    view = track_pages_module.get_results_view(context)
    assert view is not None
    assert view.message_id == 222
    assert view.session_key == "новый запрос"


def test_new_search_keeps_the_previous_view_in_history_for_back(monkeypatch, tmp_path: Path) -> None:
    context = SimpleNamespace(user_data={}, bot_data={})
    track_pages_module.set_results_view(context, _results_view())

    async def fake_send_track_card(passed_bot, chat_id: int, passed_track, **kwargs) -> SimpleNamespace:
        return SimpleNamespace(message_id=222)

    monkeypatch.setattr(track_pages_module, "carousel_keyboard", lambda **kwargs: kwargs)
    monkeypatch.setattr(track_pages_module, "send_track_card", fake_send_track_card)

    asyncio.run(
        track_pages_module.show_or_update_track_results(
            context,
            SimpleNamespace(),
            chat_id=99,
            anchor=None,
            tracks=[SimpleNamespace(id="new-track", album_id="new-album")],
            navidrome=SimpleNamespace(),
            temp_dir=tmp_path / "temp",
            header="Поиск: «новый запрос»",
            page_size=10,
            page_kind="search",
            session_key="новый запрос",
            has_next=False,
        )
    )

    history = track_pages_module.get_results_history(context)
    assert [item.session_key for item in history] == ["старый запрос"]
    assert history[0].message_id == 111


def test_same_session_still_updates_the_card_in_place(monkeypatch, tmp_path: Path) -> None:
    """Листание внутри одной выдачи по-прежнему правит карточку, а не спамит чат."""
    context = SimpleNamespace(user_data={}, bot_data={})
    track_pages_module.set_results_view(context, _results_view())

    sent: list[int] = []
    edited: list[int] = []

    async def fake_send_track_card(passed_bot, chat_id: int, passed_track, **kwargs) -> SimpleNamespace:
        sent.append(chat_id)
        return SimpleNamespace(message_id=333)

    async def fake_show_carousel_slot(ctx, passed_bot, *, slot: int, navidrome, temp_dir: Path) -> None:
        edited.append(slot)

    monkeypatch.setattr(track_pages_module, "carousel_keyboard", lambda **kwargs: kwargs)
    monkeypatch.setattr(track_pages_module, "send_track_card", fake_send_track_card)
    monkeypatch.setattr(track_pages_module, "show_carousel_slot", fake_show_carousel_slot)

    asyncio.run(
        track_pages_module.show_or_update_track_results(
            context,
            SimpleNamespace(),
            chat_id=99,
            anchor=None,
            tracks=[SimpleNamespace(id="page2-track", album_id="album")],
            navidrome=SimpleNamespace(),
            temp_dir=tmp_path / "temp",
            header="Поиск: «старый запрос»",
            page_size=10,
            page_kind="search",
            session_key="старый запрос",
            has_next=False,
        )
    )

    assert sent == [], "та же выдача — новых сообщений быть не должно"
    assert edited == [0]

    view = track_pages_module.get_results_view(context)
    assert view is not None
    assert view.message_id == 111
