from app.services.release_scoring import _days_since


def test_days_since_returns_recent_age_for_valid_utc_timestamp() -> None:
    days = _days_since("2026-07-08T00:00:00+00:00", "unused")

    assert days is not None
    assert 0.0 <= days < 2.0


def test_days_since_returns_none_for_invalid_timestamp() -> None:
    assert _days_since("not-a-timestamp", "unused") is None
