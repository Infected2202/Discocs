from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest


def _stub_module(name: str, **attrs: object) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def _load_bot_main(monkeypatch: pytest.MonkeyPatch):
    bot_pkg = _stub_module("bot")
    bot_pkg.__path__ = []
    telegram_pkg = _stub_module("telegram")
    telegram_pkg.__path__ = []

    monkeypatch.setitem(sys.modules, "bot", bot_pkg)
    monkeypatch.setitem(sys.modules, "telegram", telegram_pkg)
    monkeypatch.setitem(
        sys.modules,
        "telegram.ext",
        _stub_module(
            "telegram.ext",
            Application=object,
            CallbackQueryHandler=object,
            CommandHandler=object,
            MessageHandler=object,
            filters=SimpleNamespace(TEXT=object(), COMMAND=object(), AUDIO=object()),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "telegram.request",
        _stub_module("telegram.request", HTTPXRequest=object),
    )
    monkeypatch.setitem(sys.modules, "bot.utils.logging", _stub_module("bot.utils.logging"))
    monkeypatch.setitem(
        sys.modules,
        "bot.config",
        _stub_module("bot.config", get_settings=lambda: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "bot.handlers.callbacks",
        _stub_module("bot.handlers.callbacks", callback_handler=object()),
    )
    monkeypatch.setitem(
        sys.modules,
        "bot.handlers.menu",
        _stub_module(
            "bot.handlers.menu",
            menu_command=object(),
            menu_message_handler=object(),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "bot.handlers.random",
        _stub_module("bot.handlers.random", random_command=object()),
    )
    monkeypatch.setitem(
        sys.modules,
        "bot.handlers.search",
        _stub_module(
            "bot.handlers.search",
            audio_message_handler=object(),
            search_command=object(),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "bot.handlers.settings",
        _stub_module("bot.handlers.settings", settings_command=object()),
    )
    monkeypatch.setitem(
        sys.modules,
        "bot.handlers.start",
        _stub_module(
            "bot.handlers.start",
            help_command=object(),
            start_command=object(),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "bot.keyboards.menu",
        _stub_module("bot.keyboards.menu", BOT_COMMANDS=[]),
    )
    monkeypatch.setitem(
        sys.modules,
        "bot.services.delivery",
        _stub_module("bot.services.delivery", DeliveryService=object),
    )
    monkeypatch.setitem(
        sys.modules,
        "bot.services.discocs",
        _stub_module("bot.services.discocs", DiscocsClient=object),
    )
    monkeypatch.setitem(
        sys.modules,
        "bot.services.navidrome",
        _stub_module("bot.services.navidrome", NavidromeClient=object),
    )
    monkeypatch.setitem(
        sys.modules,
        "bot.services.transcoder",
        _stub_module("bot.services.transcoder", Transcoder=object),
    )
    monkeypatch.setitem(
        sys.modules,
        "bot.storage.db",
        _stub_module("bot.storage.db", Database=object),
    )
    monkeypatch.setitem(
        sys.modules,
        "bot.utils.single_instance",
        _stub_module("bot.utils.single_instance", acquire=lambda path: None, release=lambda path: None),
    )

    module_path = Path(__file__).resolve().parents[1] / "discocs_bot" / "bot" / "main.py"
    spec = importlib.util.spec_from_file_location("_discocs_bot_main_under_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_bot_reraises_cancelled_error_after_cleanup(monkeypatch: pytest.MonkeyPatch):
    module = _load_bot_main(monkeypatch)
    settings = SimpleNamespace(sqlite_path=Path("data") / "bot.sqlite")
    events: list[str] = []

    class FakeUpdater:
        async def start_polling(self, *, drop_pending_updates: bool) -> None:
            assert drop_pending_updates is True
            events.append("start_polling")

    class FakeApplication:
        def __init__(self) -> None:
            self.updater = FakeUpdater()

        async def initialize(self) -> None:
            events.append("initialize")

        async def start(self) -> None:
            events.append("start")

    class CancelledEvent:
        async def wait(self) -> None:
            raise asyncio.CancelledError

    async def fake_post_init(_application) -> None:
        events.append("post_init")

    async def fake_shutdown(_application) -> None:
        events.append("shutdown")

    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module, "acquire", lambda path: events.append(f"acquire:{path}"))
    monkeypatch.setattr(module, "release", lambda path: events.append(f"release:{path}"))
    monkeypatch.setattr(module, "build_application", lambda: FakeApplication())
    monkeypatch.setattr(module, "_post_init", fake_post_init)
    monkeypatch.setattr(module, "_shutdown", fake_shutdown)
    monkeypatch.setattr(module.asyncio, "Event", CancelledEvent)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(module._run_bot())

    assert events == [
        "acquire:data",
        "initialize",
        "post_init",
        "start",
        "start_polling",
        "shutdown",
        "release:data",
    ]
