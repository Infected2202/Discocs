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


def test_refresh_skipped_when_multiuser_auth_enabled(monkeypatch):
    calls = _patch_refresh(monkeypatch)
    settings = _settings(play_state_refresh_seconds=60)
    settings = replace(settings, auth=replace(settings.auth, enabled=True))

    maintenance._maybe_refresh_navidrome_play_state(store=object(), settings=settings)

    assert calls == []


class _FakeStore:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def expire_analysis_leases(self) -> None:
        self.calls.append("expire_analysis_leases")

    def refresh_active_analysis_jobs(self) -> None:
        self.calls.append("refresh_active_analysis_jobs")

    def recent_analysis_jobs(self, limit: int) -> list:
        self.calls.append("recent_analysis_jobs")
        return []

    def albums_for_you_cache_age_hours(self, model_name: str) -> float:
        self.calls.append("albums_for_you_cache_age_hours")
        return 999.0  # older than refresh threshold so refresh is attempted


def test_run_maintenance_tick_uses_passed_in_store(monkeypatch):
    """An explicitly passed store must be used as-is, not discarded for a fresh context() store."""
    fake_store = _FakeStore()
    settings = _settings(play_state_refresh_seconds=0)

    def _fail_context():
        raise AssertionError("context() must not be called when a store is explicitly provided")

    monkeypatch.setattr(maintenance, "context", _fail_context)
    monkeypatch.setattr(maintenance.Settings, "from_env", staticmethod(lambda: settings))
    monkeypatch.setattr(maintenance, "sync_memory_jobs_from_durable_jobs", lambda jobs: None)
    monkeypatch.setattr(maintenance, "maybe_start_next_deferred_job", lambda: None)
    monkeypatch.setattr("app.services.albums_for_you.refresh_albums_for_you", lambda store, model: None)
    monkeypatch.setattr(maintenance, "_maybe_refresh_generated_mixes", lambda *_args: None)
    monkeypatch.setattr(maintenance, "_maybe_refresh_flow_profile", lambda *_args: None)

    maintenance.run_maintenance_tick(fake_store)

    assert "expire_analysis_leases" in fake_store.calls
    assert "albums_for_you_cache_age_hours" in fake_store.calls


def test_unscoped_maintenance_refreshes_each_user_store(monkeypatch):
    root = _FakeStore()
    root.user_id = None
    root.list_user_ids = lambda: [11, 22]
    scoped = {user_id: object() for user_id in root.list_user_ids()}
    root.for_user = lambda user_id: scoped[user_id]
    settings = _settings(play_state_refresh_seconds=0)
    refreshed: list[tuple[str, object]] = []

    monkeypatch.setattr(maintenance.Settings, "from_env", staticmethod(lambda: settings))
    monkeypatch.setattr(maintenance, "sync_memory_jobs_from_durable_jobs", lambda jobs: None)
    monkeypatch.setattr(maintenance, "maybe_start_next_deferred_job", lambda: None)
    monkeypatch.setattr(
        maintenance,
        "_maybe_refresh_albums_for_you",
        lambda store, _settings: refreshed.append(("albums", store)),
    )
    monkeypatch.setattr(
        maintenance,
        "_maybe_refresh_generated_mixes",
        lambda store, _settings: refreshed.append(("mixes", store)),
    )
    monkeypatch.setattr(
        maintenance,
        "_maybe_refresh_flow_profile",
        lambda store, _settings: refreshed.append(("flow", store)),
    )

    maintenance.run_maintenance_tick(root)

    assert refreshed == [
        ("albums", scoped[11]),
        ("mixes", scoped[11]),
        ("flow", scoped[11]),
        ("albums", scoped[22]),
        ("mixes", scoped[22]),
        ("flow", scoped[22]),
    ]
