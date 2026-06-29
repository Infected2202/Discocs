from __future__ import annotations

from dataclasses import replace

import app.maintenance as maintenance
from app.config import NavidromeSettings, Settings


def _settings(**nav_overrides) -> Settings:
    from pathlib import Path

    nav = NavidromeSettings(url="http://navidrome:4533", user="a", password="b")
    nav = replace(nav, **nav_overrides)
    return Settings(
        data_dir=Path("data"),
        db_path=Path("data/app.db"),
        model_dir=Path("models"),
        index_dir=Path("data"),
        navidrome=nav,
    )


def _patch_refresh(monkeypatch) -> list[int]:
    calls: list[int] = []
    monkeypatch.setattr(maintenance, "_last_play_state_refresh", None, raising=False)
    monkeypatch.setattr("app.navidrome.NavidromeClient", lambda nav: ("client", nav))

    def fake_refresh(store, client, *, album_count):
        calls.append(album_count)

    monkeypatch.setattr("app.navidrome_sync.refresh_navidrome_play_state", fake_refresh)
    return calls


def test_refresh_throttled_to_interval(monkeypatch):
    calls = _patch_refresh(monkeypatch)
    now = [1000.0]
    monkeypatch.setattr(maintenance, "monotonic", lambda: now[0])
    settings = _settings(play_state_refresh_seconds=60, play_state_refresh_albums=7)

    maintenance._maybe_refresh_navidrome_play_state(store=object(), settings=settings)
    assert calls == [7]  # first call runs immediately

    now[0] = 1030.0  # 30s later — within interval, throttled
    maintenance._maybe_refresh_navidrome_play_state(store=object(), settings=settings)
    assert calls == [7]

    now[0] = 1065.0  # >60s — runs again
    maintenance._maybe_refresh_navidrome_play_state(store=object(), settings=settings)
    assert calls == [7, 7]


def test_refresh_disabled_when_interval_zero(monkeypatch):
    calls = _patch_refresh(monkeypatch)
    monkeypatch.setattr(maintenance, "monotonic", lambda: 1.0)
    settings = _settings(play_state_refresh_seconds=0)

    maintenance._maybe_refresh_navidrome_play_state(store=object(), settings=settings)
    assert calls == []


def test_refresh_skipped_without_navidrome_url(monkeypatch):
    calls = _patch_refresh(monkeypatch)
    monkeypatch.setattr(maintenance, "monotonic", lambda: 1.0)
    settings = _settings(url="", play_state_refresh_seconds=60)

    maintenance._maybe_refresh_navidrome_play_state(store=object(), settings=settings)
    assert calls == []
