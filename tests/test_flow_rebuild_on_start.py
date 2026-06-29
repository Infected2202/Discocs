from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import app.api.flow as flow


@dataclass
class _Profile:
    model_key: str = "discogs_multi"
    status: str = "ready"
    last_built_at: str | None = None


def _iso(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def test_profile_age_minutes_parses_recent():
    age = flow._profile_age_minutes(_Profile(last_built_at=_iso(10)))
    assert age is not None
    assert 9 < age < 11


def test_profile_age_minutes_none_when_never_built():
    assert flow._profile_age_minutes(_Profile(last_built_at=None)) is None


def test_profile_age_handles_z_suffix():
    stamp = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    age = flow._profile_age_minutes(_Profile(last_built_at=stamp))
    assert age is not None and 4 < age < 6


def test_rebuild_when_stale():
    store = MagicMock()
    rebuilt = _Profile(last_built_at=_iso(0))
    store.get_flow_profile.return_value = rebuilt
    stale = _Profile(last_built_at=_iso(120))

    with patch("app.services.flow_regions.rebuild_flow_profile") as rb:
        result = flow._maybe_rebuild_stale_profile(store, MagicMock(), stale, {})

    rb.assert_called_once()
    assert result is rebuilt


def test_no_rebuild_when_fresh():
    store = MagicMock()
    fresh = _Profile(last_built_at=_iso(5))

    with patch("app.services.flow_regions.rebuild_flow_profile") as rb:
        result = flow._maybe_rebuild_stale_profile(store, MagicMock(), fresh, {})

    rb.assert_not_called()
    assert result is fresh


def test_no_rebuild_when_disabled():
    store = MagicMock()
    stale = _Profile(last_built_at=_iso(999))

    with patch("app.services.flow_regions.rebuild_flow_profile") as rb:
        result = flow._maybe_rebuild_stale_profile(
            store, MagicMock(), stale, {"rebuild_max_age_minutes": 0}
        )

    rb.assert_not_called()
    assert result is stale


def test_no_rebuild_when_profile_not_ready():
    store = MagicMock()
    building = _Profile(status="building", last_built_at=_iso(999))

    with patch("app.services.flow_regions.rebuild_flow_profile") as rb:
        result = flow._maybe_rebuild_stale_profile(store, MagicMock(), building, {})

    rb.assert_not_called()
    assert result is building


def test_rebuild_failure_keeps_old_profile():
    store = MagicMock()
    stale = _Profile(last_built_at=_iso(120))

    with patch("app.services.flow_regions.rebuild_flow_profile", side_effect=RuntimeError("boom")):
        result = flow._maybe_rebuild_stale_profile(store, MagicMock(), stale, {})

    assert result is stale
