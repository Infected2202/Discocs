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
