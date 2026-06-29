from __future__ import annotations

from datetime import datetime, timedelta

try:
    from datetime import UTC
except ImportError:  # pragma: no cover - py<3.11
    from datetime import timezone as _tz

    UTC = _tz.utc

from app.scanner import ScannedTrack
from app.store import Store


def _add_track(store: Store, path: str) -> int:
    track_id, _ = store.upsert_track(
        ScannedTrack(
            path=path, artist="A", title=path, album="Alb",
            duration=100.0, file_size=10, mtime=1,
        )
    )
    return track_id


def _iso(days_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()


def test_recently_played_includes_recent_excludes_old(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    recent = _add_track(store, "navidrome://recent")
    old = _add_track(store, "navidrome://old")
    never = _add_track(store, "navidrome://never")

    store.import_external_track_play_state(recent, play_count=1, last_played_at=_iso(1))
    store.import_external_track_play_state(old, play_count=1, last_played_at=_iso(30))

    result = store.recently_played_track_ids(within_days=7)

    assert recent in result
    assert old not in result
    assert never not in result


def test_recently_played_handles_navidrome_z_suffix(tmp_path):
    """Navidrome timestamps end in 'Z'; the suffix-free cutoff must still match."""
    store = Store(tmp_path / "app.db")
    store.init()
    tid = _add_track(store, "navidrome://z")
    stamp = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    store.import_external_track_play_state(tid, play_count=1, last_played_at=stamp)

    assert tid in store.recently_played_track_ids(within_days=7)


def test_recently_played_zero_days_returns_empty(tmp_path):
    store = Store(tmp_path / "app.db")
    store.init()
    tid = _add_track(store, "navidrome://x")
    store.import_external_track_play_state(tid, play_count=1, last_played_at=_iso(0))

    assert store.recently_played_track_ids(within_days=0) == set()
