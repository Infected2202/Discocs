"""Profile API (social Ф3): period stats in the viewer's timezone, tops, sound
profile, listening history, likes, playlists — and the privacy rules."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import app
from app.models import utc_now
from app.scanner import ScannedTrack
from app.services.profile import (
    GENRE_MODEL,
    MOOD_MODEL,
    TOP_ARTISTS_LIMIT,
    TOP_RELEASES_LIMIT,
    ProfileNotFoundError,
    ProfileViewerRequiredError,
    bucket_listens,
    profile_likes,
    profile_likes_of_kind,
    profile_listens,
    profile_playlists_payload,
    profile_stats,
    profile_top,
    profile_window,
    resolve_timezone,
)
from app.services.shelves import SHELF_PREVIEW_LIMIT
from app.store import INITIALIZED_DB_PATHS, PlaybackEventCreate, Store

NOW = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stores(tmp_path: Path) -> tuple[Store, Store, Store]:
    root = Store(tmp_path / "app.db")
    root.init()
    alice = root.for_user(root.upsert_user("Alice", now=utc_now()))
    bob = root.for_user(root.upsert_user("bob", now=utc_now()))
    return root, alice, bob


def _track(
    store: Store,
    tmp_path: Path,
    name: str,
    *,
    artist: str = "Solo",
    album: str | None = None,
    album_artist: str | None = None,
    duration: float = 200.0,
) -> int:
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=(tmp_path / f"{name}.flac").resolve(),
            artist=artist,
            title=name,
            album=album or f"{name} album",
            duration=duration,
            file_size=1,
            mtime=1,
            album_artist=album_artist or artist,
        )
    )
    return track_id


def _at(**delta: float) -> str:
    """A UTC timestamp relative to NOW, formatted like ``listened_at``."""
    return (NOW - timedelta(**delta)).isoformat()


def _listen(store: Store, user_store: Store, track_id: int, at: str) -> None:
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO listens (user_id, track_id, listened_at, event_id) VALUES (?, ?, ?, ?)",
            (user_store.user_id, track_id, at, uuid4().hex),
        )


def _predict(store: Store, track_id: int, model_name: str, label: str, rank: int = 1) -> None:
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO track_predictions (track_id, model_name, label, score, rank, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (track_id, model_name, label, 1.0 / rank, rank, utc_now()),
        )


def _release_of(store: Store, track_id: int) -> int:
    with store.connect() as conn:
        return int(
            conn.execute("SELECT release_id FROM release_tracks WHERE track_id = ?", (track_id,)).fetchone()[0]
        )


def _stats(viewer: Store, username: str = "alice", **kwargs) -> dict:
    kwargs.setdefault("now", NOW)
    return profile_stats(viewer, username, **kwargs)


def _day_counts(stats: dict) -> dict[str, int]:
    return {entry["date"]: entry["listens"] for entry in stats["by_day"]}


def _all_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.add(str(key))
            keys |= _all_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            keys |= _all_keys(nested)
    return keys


# ---------------------------------------------------------------------------
# Period window and timezone
# ---------------------------------------------------------------------------

def test_period_window_starts_at_local_midnight_n_days_back():
    utc_week = profile_window("7d", "UTC", NOW)
    moscow_week = profile_window("7d", "Europe/Moscow", NOW)

    assert utc_week.since == "2026-09-28T00:00:00+00:00"
    assert moscow_week.since == "2026-09-27T21:00:00+00:00"
    assert utc_week.until == moscow_week.until == NOW.isoformat()
    assert profile_window("30d", "UTC", NOW).since == "2026-09-05T00:00:00+00:00"
    assert profile_window("all", "UTC", NOW).since is None
    with pytest.raises(ValueError):
        profile_window("14d", "UTC", NOW)


def test_invalid_or_missing_timezone_falls_back_to_utc():
    for bad in (None, "", "Mars/Olympus", "../../etc/passwd", "Europe/" + "x" * 80, "\x00"):
        name, zone = resolve_timezone(bad)
        assert name == "UTC", bad
        assert zone is UTC
    name, zone = resolve_timezone(" Europe/Moscow ")
    assert name == "Europe/Moscow"
    assert datetime(2026, 1, 1, tzinfo=zone).utcoffset() == timedelta(hours=3)


def test_period_boundaries_are_inclusive_at_start(tmp_path: Path):
    root, alice, _bob = _stores(tmp_path)
    track = _track(root, tmp_path, "edge")
    _listen(root, alice, track, "2026-09-27T23:59:59+00:00")  # one second before the 7d window
    _listen(root, alice, track, "2026-09-28T00:00:00+00:00")  # exactly the start
    _listen(root, alice, track, _at(hours=1))

    week = _stats(alice, period="7d", tz_name="UTC")
    month = _stats(alice, period="30d", tz_name="UTC")

    assert week["summary"]["listens"] == 2
    assert month["summary"]["listens"] == 3
    assert [entry["date"] for entry in week["by_day"]] == [
        f"2026-{month_day}" for month_day in ("09-28", "09-29", "09-30", "10-01", "10-02", "10-03", "10-04")
    ]
    assert _day_counts(week)["2026-09-28"] == 1
    assert _day_counts(week)["2026-10-04"] == 1
    assert sum(_day_counts(week).values()) == 2
    assert len(month["by_day"]) == 30
    assert week["period"] == {
        "key": "7d",
        "tz": "UTC",
        "since": "2026-09-28T00:00:00+00:00",
        "until": NOW.isoformat(),
    }


def test_day_and_hour_buckets_follow_the_viewer_timezone(tmp_path: Path):
    root, alice, _bob = _stores(tmp_path)
    track = _track(root, tmp_path, "late")
    _listen(root, alice, track, "2026-10-02T23:30:00+00:00")  # 02:30 on Oct 3 in Moscow
    _listen(root, alice, track, "2026-09-27T21:30:00+00:00")  # 00:30 on Sep 28 in Moscow

    utc = _stats(alice, period="7d", tz_name="UTC")
    moscow = _stats(alice, period="7d", tz_name="Europe/Moscow")
    invalid = _stats(alice, period="7d", tz_name="Not/AZone")

    assert _day_counts(utc)["2026-10-02"] == 1
    assert _day_counts(utc)["2026-10-03"] == 0
    assert _day_counts(moscow)["2026-10-02"] == 0
    assert _day_counts(moscow)["2026-10-03"] == 1
    # The Sep 27 21:30 UTC listen is inside the Moscow week only.
    assert utc["summary"]["listens"] == 1
    assert moscow["summary"]["listens"] == 2
    assert _day_counts(moscow)["2026-09-28"] == 1
    assert utc["by_hour"][23] == 1 and utc["by_hour"][2] == 0
    assert moscow["by_hour"][2] == 1 and moscow["by_hour"][0] == 1 and moscow["by_hour"][23] == 0
    assert len(moscow["by_hour"]) == 24
    assert invalid["period"]["tz"] == "UTC"
    assert invalid["by_hour"] == utc["by_hour"]
    assert invalid["by_day"] == utc["by_day"]


def test_all_time_starts_at_first_listen_and_long_spans_bucket_by_month(tmp_path: Path):
    root, alice, bob = _stores(tmp_path)
    track = _track(root, tmp_path, "old")
    _listen(root, alice, track, "2026-09-30T12:00:00+00:00")
    _listen(root, alice, track, "2026-10-01T12:00:00+00:00")

    short = _stats(alice, period="all", tz_name="UTC")

    assert short["by_day_bucket"] == "day"
    assert [entry["date"] for entry in short["by_day"]] == ["2026-09-30", "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]
    assert _stats(bob, "bob", period="all", tz_name="UTC")["by_day"] == []

    _listen(root, alice, track, "2025-08-15T12:00:00+00:00")
    long = _stats(alice, period="all", tz_name="UTC")

    assert long["by_day_bucket"] == "month"
    assert long["by_day"][0] == {"date": "2025-08-01", "listens": 1}
    assert long["by_day"][-2] == {"date": "2026-09-01", "listens": 1}
    assert long["by_day"][-1] == {"date": "2026-10-01", "listens": 1}
    assert len(long["by_day"]) == 15  # Aug 2025 … Oct 2026, empty months included
    assert sum(entry["listens"] for entry in long["by_day"]) == 3


def test_bucket_listens_counts_every_listen_once():
    bucket, by_day, by_hour = bucket_listens(
        ["2026-10-01T05:00:00+00:00", "2026-10-01T05:59:00+00:00", "2026-10-03T23:00:00Z"],
        UTC,
        datetime(2026, 10, 1).date(),
        datetime(2026, 10, 3).date(),
    )

    assert bucket == "day"
    assert by_day == [
        {"date": "2026-10-01", "listens": 2},
        {"date": "2026-10-02", "listens": 0},
        {"date": "2026-10-03", "listens": 1},
    ]
    assert by_hour[5] == 2 and by_hour[23] == 1 and sum(by_hour) == 3


# ---------------------------------------------------------------------------
# Totals, summary, tops, sound profile
# ---------------------------------------------------------------------------

def test_totals_are_all_time_and_summary_is_the_period(tmp_path: Path):
    root, alice, bob = _stores(tmp_path)
    duet = _track(root, tmp_path, "duet", artist="Alpha feat. Beta", duration=1800.0)
    long_one = _track(root, tmp_path, "long", artist="Gamma", duration=2700.0)
    _listen(root, alice, duet, _at(days=100))
    _listen(root, alice, long_one, _at(days=2))
    _listen(root, alice, long_one, _at(days=1))
    alice.set_track_liked(duet, True)

    stats = _stats(alice, period="30d", tz_name="UTC")

    assert stats["header"]["totals"] == {"listens": 3, "artists": 3, "likes": 1}
    assert stats["summary"] == {"listens": 2, "hours": 1.5, "artists": 1}
    assert _stats(alice, period="365d", tz_name="UTC")["summary"] == {"listens": 3, "hours": 2.0, "artists": 3}
    # Two-user isolation: Bob listening and liking changes nothing for Alice.
    _listen(root, bob, duet, _at(days=1))
    _listen(root, bob, long_one, _at(days=1))
    bob.set_track_liked(long_one, True)
    assert _stats(alice, period="30d", tz_name="UTC")["summary"] == stats["summary"]
    assert _stats(bob, "alice", period="30d", tz_name="UTC")["header"]["totals"] == stats["header"]["totals"]
    assert _stats(bob, "bob", period="30d", tz_name="UTC")["header"]["totals"] == {
        "listens": 2,
        "artists": 3,
        "likes": 1,
    }


def test_listens_of_tracks_gone_from_the_library_are_ignored(tmp_path: Path):
    root, alice, _bob = _stores(tmp_path)
    kept = _track(root, tmp_path, "kept")
    _listen(root, alice, kept, _at(hours=2))
    _listen(root, alice, 999_999, _at(hours=1))

    stats = _stats(alice, period="7d", tz_name="UTC")
    history = profile_listens(alice, "alice", limit=10, offset=0)

    assert stats["header"]["totals"]["listens"] == 1
    assert stats["summary"]["listens"] == 1
    assert [item["id"] for item in stats["recent"]] == [kept]
    assert history["total"] == 1
    assert [item["id"] for item in history["items"]] == [kept]


def test_top_lists_order_by_listens_then_recency_then_id(tmp_path: Path):
    root, alice, _bob = _stores(tmp_path)
    most = _track(root, tmp_path, "most", artist="Alpha feat. Beta", album="Shared")
    recent_pair = _track(root, tmp_path, "recent-pair", artist="Gamma")
    older_pair = _track(
        root, tmp_path, "older-pair", artist="Alpha", album="Shared", album_artist="Alpha feat. Beta"
    )
    tie_low = _track(root, tmp_path, "tie-low", artist="Delta")
    tie_high = _track(root, tmp_path, "tie-high", artist="Epsilon")
    for hours in (10, 9, 8):
        _listen(root, alice, most, _at(hours=hours))
    _listen(root, alice, older_pair, _at(hours=7))
    _listen(root, alice, older_pair, _at(hours=6))
    _listen(root, alice, recent_pair, _at(hours=5))
    _listen(root, alice, recent_pair, _at(hours=1))
    _listen(root, alice, tie_high, _at(hours=3))
    _listen(root, alice, tie_low, _at(hours=3))

    stats = _stats(alice, period="7d", tz_name="UTC")

    tracks = [(item["id"], item["listens"]) for item in stats["top_tracks"]]
    assert tracks == [(most, 3), (recent_pair, 2), (older_pair, 2), (min(tie_low, tie_high), 1), (max(tie_low, tie_high), 1)]
    # Multi-artist track counts for every credited artist.
    artists = [(item["title"], item["listens"]) for item in stats["top_artists"]]
    assert artists[:3] == [("Alpha", 5), ("Beta", 3), ("Gamma", 2)]
    shared = _release_of(root, most)
    assert _release_of(root, older_pair) == shared
    releases = [(item["entity_id"], item["listens"]) for item in stats["top_releases"]]
    assert releases[0] == (shared, 5)
    assert releases[1] == (_release_of(root, recent_pair), 2)
    # Shelf-item shapes the UI renders with MediaCard / VirtualTrackList.
    artist_item = stats["top_artists"][0]
    assert artist_item["entity_type"] == "artist"
    assert artist_item["action"] == {"type": "open", "target": f"/artists/{artist_item['entity_id']}"}
    assert stats["top_releases"][0]["entity_type"] == "release"
    assert {"id", "title", "artists", "duration", "release", "artwork", "listens"} <= set(stats["top_tracks"][0])


def test_top_lists_respect_the_period(tmp_path: Path):
    root, alice, _bob = _stores(tmp_path)
    old_favourite = _track(root, tmp_path, "old-favourite", artist="Old")
    new_one = _track(root, tmp_path, "new-one", artist="New")
    for days in (40, 41, 42):
        _listen(root, alice, old_favourite, _at(days=days))
    _listen(root, alice, new_one, _at(days=1))

    week = _stats(alice, period="7d", tz_name="UTC")
    quarter = _stats(alice, period="90d", tz_name="UTC")

    assert [item["id"] for item in week["top_tracks"]] == [new_one]
    assert [item["title"] for item in week["top_artists"]] == ["New"]
    assert [item["id"] for item in quarter["top_tracks"]] == [old_favourite, new_one]


def test_top_tracks_are_capped_at_five(tmp_path: Path):
    root, alice, _bob = _stores(tmp_path)
    tracks = [_track(root, tmp_path, f"t{n}", artist=f"Artist {n}") for n in range(7)]
    # t0 is played 7 times, t1 six times, … t6 once: a strict ranking.
    for rank, track_id in enumerate(tracks):
        for hours in range(len(tracks) - rank):
            _listen(root, alice, track_id, _at(hours=hours + 1, minutes=rank))

    stats = _stats(alice, period="7d", tz_name="UTC")

    assert [item["id"] for item in stats["top_tracks"]] == tracks[:5]
    # The full length, so the list links to its full page only when there is more.
    assert stats["top_tracks_total"] == 7


def _ranked_artists(root: Store, alice: Store, tmp_path: Path, count: int) -> list[int]:
    """``count`` tracks by distinct artists; track n is played ``count - n`` times
    within the last day, so the top order is the creation order."""
    tracks = [_track(root, tmp_path, f"rank{n}", artist=f"Rank Artist {n:02d}") for n in range(count)]
    for rank, track_id in enumerate(tracks):
        for repeat in range(count - rank):
            _listen(root, alice, track_id, _at(hours=1, minutes=repeat))
    return tracks


def test_profile_tops_preview_the_common_shelf_size_and_report_full_totals(tmp_path: Path):
    root, alice, _bob = _stores(tmp_path)
    tracks = _ranked_artists(root, alice, tmp_path, SHELF_PREVIEW_LIMIT + 3)

    stats = _stats(alice, period="7d", tz_name="UTC")

    assert TOP_ARTISTS_LIMIT == TOP_RELEASES_LIMIT == SHELF_PREVIEW_LIMIT == 16
    assert len(stats["top_artists"]) == SHELF_PREVIEW_LIMIT
    assert len(stats["top_releases"]) == SHELF_PREVIEW_LIMIT
    assert stats["top_artists_total"] == len(tracks)
    assert stats["top_releases_total"] == len(tracks)


def test_profile_top_pages_continue_the_preview_in_the_same_order(tmp_path: Path):
    root, alice, bob = _stores(tmp_path)
    tracks = _ranked_artists(root, alice, tmp_path, 7)
    preview = _stats(alice, period="7d", tz_name="UTC")

    first = profile_top(bob, "ALICE", "artists", period="7d", tz_name="UTC", limit=3, offset=0, now=NOW)
    last = profile_top(bob, "alice", "artists", period="7d", tz_name="UTC", limit=3, offset=6, now=NOW)
    releases = profile_top(bob, "alice", "releases", period="7d", tz_name="UTC", limit=50, now=NOW)

    assert [item["title"] for item in first["items"]] == ["Rank Artist 00", "Rank Artist 01", "Rank Artist 02"]
    assert [item["listens"] for item in first["items"]] == [7, 6, 5]
    assert (first["total"], first["limit"], first["offset"], first["next_offset"]) == (7, 3, 0, 3)
    assert [item["title"] for item in last["items"]] == ["Rank Artist 06"]
    assert last["next_offset"] is None
    assert first["period"]["key"] == "7d" and first["period"]["tz"] == "UTC"
    assert [item["entity_id"] for item in releases["items"]] == [_release_of(root, t) for t in tracks]
    assert [item["entity_id"] for item in releases["items"]] == [
        item["entity_id"] for item in preview["top_releases"]
    ]
    assert releases["total"] == 7 and releases["next_offset"] is None


def test_profile_top_respects_the_period(tmp_path: Path):
    root, alice, _bob = _stores(tmp_path)
    old = _track(root, tmp_path, "old-top", artist="Old")
    new = _track(root, tmp_path, "new-top", artist="New")
    _listen(root, alice, old, _at(days=40))
    _listen(root, alice, new, _at(days=1))

    week = profile_top(alice, "alice", "artists", period="7d", tz_name="UTC", limit=10, now=NOW)
    quarter = profile_top(alice, "alice", "releases", period="90d", tz_name="UTC", limit=10, now=NOW)
    week_tracks = profile_top(alice, "alice", "tracks", period="7d", tz_name="UTC", limit=10, now=NOW)

    assert [item["title"] for item in week["items"]] == ["New"]
    assert week["total"] == 1
    assert {item["entity_id"] for item in quarter["items"]} == {_release_of(root, old), _release_of(root, new)}
    assert quarter["total"] == 2
    assert [(item["id"], item["listens"]) for item in week_tracks["items"]] == [(new, 1)]
    assert week_tracks["total"] == 1
    with pytest.raises(ValueError):
        profile_top(alice, "alice", "labels", limit=10, now=NOW)


def test_sound_profile_is_weighted_by_listens(tmp_path: Path):
    root, alice, _bob = _stores(tmp_path)
    techno = _track(root, tmp_path, "techno")
    punk_one = _track(root, tmp_path, "punk-one")
    punk_two = _track(root, tmp_path, "punk-two")
    unanalysed = _track(root, tmp_path, "unanalysed")
    _predict(root, techno, GENRE_MODEL, "Electronic---Techno")
    _predict(root, techno, GENRE_MODEL, "Jazz---Bop", rank=2)
    _predict(root, punk_one, GENRE_MODEL, "Rock---Punk")
    _predict(root, punk_two, GENRE_MODEL, "Rock---Punk")
    _predict(root, techno, MOOD_MODEL, "energetic")
    _predict(root, punk_one, MOOD_MODEL, "dark")
    for hours in (1, 2, 3, 4):
        _listen(root, alice, techno, _at(hours=hours))
    _listen(root, alice, punk_one, _at(hours=5))
    _listen(root, alice, unanalysed, _at(hours=6))
    _listen(root, alice, punk_two, _at(days=60))  # outside the 30d period

    sound = _stats(alice, period="30d", tz_name="UTC")["sound"]

    # One techno track played 4× outweighs the punk tracks; rank-2 labels and
    # tracks without predictions are not counted.
    assert sound["genres"] == [
        {"label": "Electronic---Techno", "listens": 4, "share": 0.8, "genre": "Electronic", "style": "Techno"},
        {"label": "Rock---Punk", "listens": 1, "share": 0.2, "genre": "Rock", "style": "Punk"},
    ]
    assert sound["moods"] == [
        {"label": "energetic", "listens": 4, "share": 0.8},
        {"label": "dark", "listens": 1, "share": 0.2},
    ]
    all_time = _stats(alice, period="all", tz_name="UTC")["sound"]["genres"]
    assert [(item["label"], item["listens"]) for item in all_time] == [
        ("Electronic---Techno", 4),
        ("Rock---Punk", 2),
    ]


def test_sound_profile_is_empty_without_predictions(tmp_path: Path):
    root, alice, _bob = _stores(tmp_path)
    _listen(root, alice, _track(root, tmp_path, "plain"), _at(hours=1))

    assert _stats(alice, period="30d", tz_name="UTC")["sound"] == {"genres": [], "moods": []}


# ---------------------------------------------------------------------------
# Listening history
# ---------------------------------------------------------------------------

def test_listens_are_paged_newest_first_with_repeats(tmp_path: Path):
    root, alice, bob = _stores(tmp_path)
    first = _track(root, tmp_path, "first")
    second = _track(root, tmp_path, "second")
    for hours, track in ((5, first), (4, second), (3, first), (2, first), (1, second)):
        _listen(root, alice, track, _at(hours=hours))
    _listen(root, bob, first, _at(minutes=1))

    page_one = profile_listens(bob, "ALICE", limit=2, offset=0)
    last_page = profile_listens(bob, "alice", limit=2, offset=4)

    assert page_one["total"] == 5
    assert [item["id"] for item in page_one["items"]] == [second, first]
    assert [item["listened_at"] for item in page_one["items"]] == [_at(hours=1), _at(hours=2)]
    assert page_one["next_offset"] == 2
    assert [item["id"] for item in last_page["items"]] == [first]
    assert last_page["next_offset"] is None
    everything = profile_listens(alice, "alice", limit=100, offset=0)["items"]
    assert [item["id"] for item in everything] == [second, first, first, second, first]
    assert len({item["listen_id"] for item in everything}) == 5
    recent = _stats(alice, period="7d", tz_name="UTC")["recent"]
    assert [item["listen_id"] for item in recent] == [item["listen_id"] for item in everything]


# ---------------------------------------------------------------------------
# Likes (target-scoped)
# ---------------------------------------------------------------------------

def test_likes_are_the_targets_own(tmp_path: Path):
    root, alice, bob = _stores(tmp_path)
    liked_track = _track(root, tmp_path, "liked", artist="Liked Artist")
    bobs_track = _track(root, tmp_path, "bobs")
    alice.set_track_liked(liked_track, True)
    alice.set_release_liked(_release_of(root, liked_track), True)
    with root.connect() as conn:
        artist_id = int(conn.execute("SELECT id FROM artists WHERE name = 'Liked Artist'").fetchone()[0])
    alice.set_artist_liked(artist_id, True)
    bob.set_track_liked(bobs_track, True)

    seen_by_bob = profile_likes(bob, "alice", limit=50)

    assert [item["entity_id"] for item in seen_by_bob["tracks"]["items"]] == [liked_track]
    assert seen_by_bob["tracks"]["total"] == 1
    assert [item["entity_id"] for item in seen_by_bob["releases"]["items"]] == [_release_of(root, liked_track)]
    assert [item["entity_id"] for item in seen_by_bob["artists"]["items"]] == [artist_id]
    assert seen_by_bob["artists"]["items"][0]["entity_type"] == "artist"
    assert profile_likes(alice, "bob", limit=50)["tracks"]["items"][0]["entity_id"] == bobs_track


def test_likes_of_one_kind_are_paged_with_totals(tmp_path: Path):
    root, alice, bob = _stores(tmp_path)
    tracks = [_track(root, tmp_path, f"like{n}", artist=f"Like Artist {n}") for n in range(5)]
    for track in tracks:
        alice.set_track_liked(track, True)
        alice.set_release_liked(_release_of(root, track), True)
    bob.set_track_liked(tracks[0], True)

    first = profile_likes_of_kind(bob, "alice", "tracks", limit=2, offset=0)
    middle = profile_likes_of_kind(bob, "alice", "tracks", limit=2, offset=2)
    rest = profile_likes_of_kind(bob, "alice", "tracks", limit=2, offset=4)
    releases = profile_likes_of_kind(bob, "alice", "releases", limit=3, offset=0)
    artists = profile_likes_of_kind(bob, "alice", "artists", limit=3, offset=0)

    assert (first["total"], first["limit"], first["offset"], first["next_offset"]) == (5, 2, 0, 2)
    assert len(first["items"]) == 2 and first["items"][0]["entity_type"] == "track"
    assert len(rest["items"]) == 1 and rest["next_offset"] is None
    paged = [item["entity_id"] for page in (first, middle, rest) for item in page["items"]]
    assert sorted(paged) == sorted(tracks)
    assert releases["total"] == 5 and releases["next_offset"] == 3
    assert releases["items"][0]["entity_type"] == "release"
    assert artists["total"] == 0 and artists["items"] == [] and artists["next_offset"] is None
    # Bob's own like is not on Alice's list and vice versa.
    assert profile_likes_of_kind(alice, "bob", "tracks", limit=10)["total"] == 1
    with pytest.raises(ValueError):
        profile_likes_of_kind(alice, "alice", "playlists", limit=10)


def test_playlists_are_paged_and_private_ones_stay_hidden(tmp_path: Path):
    _root, alice, bob = _stores(tmp_path)
    for n in range(3):
        alice.create_playlist(title=f"Public {n}", source={"visibility": "public"})
    alice.create_playlist(title="Secret")

    seen_by_bob = profile_playlists_payload(bob, "alice", limit=2, offset=0)
    bob_rest = profile_playlists_payload(bob, "alice", limit=2, offset=2)
    own = profile_playlists_payload(alice, "alice", limit=2, offset=0)
    everything = profile_playlists_payload(bob, "alice")

    assert (seen_by_bob["total"], seen_by_bob["limit"], seen_by_bob["next_offset"]) == (3, 2, 2)
    assert len(seen_by_bob["items"]) == 2 and len(bob_rest["items"]) == 1
    assert bob_rest["next_offset"] is None
    titles = {item["title"] for item in seen_by_bob["items"] + bob_rest["items"]}
    assert titles == {"Public 0", "Public 1", "Public 2"}
    assert own["total"] == 4 and own["next_offset"] == 2
    assert everything["total"] == 3 and len(everything["items"]) == 3 and everything["next_offset"] is None


def test_service_principal_and_unknown_users_are_refused(tmp_path: Path):
    _root, alice, _bob = _stores(tmp_path)
    service = Store(tmp_path / "app.db", user_id=None)

    with pytest.raises(ProfileViewerRequiredError):
        profile_stats(service, "alice")
    with pytest.raises(ProfileViewerRequiredError):
        profile_listens(service, "alice", limit=10, offset=0)
    with pytest.raises(ProfileNotFoundError):
        profile_stats(alice, "carol")
    with pytest.raises(ProfileNotFoundError):
        profile_likes(alice, "carol", limit=10)
    with pytest.raises(ProfileNotFoundError):
        profile_top(alice, "carol", "artists", limit=10)
    with pytest.raises(ProfileNotFoundError):
        profile_likes_of_kind(alice, "carol", "tracks", limit=10)
    with pytest.raises(ProfileViewerRequiredError):
        profile_top(service, "alice", "releases", limit=10)


# ---------------------------------------------------------------------------
# API (auth enabled, two real sessions)
# ---------------------------------------------------------------------------

def _init_auth_store(tmp_path: Path, monkeypatch) -> Store:
    db_path = tmp_path / "app.db"
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    monkeypatch.setenv("DISCOCS_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("DISCOCS_AUTH_ENABLED", "true")
    monkeypatch.setenv("DISCOCS_NAVIDROME_URL", "http://navidrome:4533")
    monkeypatch.setenv("DISCOCS_SERVICE_TOKEN", "svc-secret")
    monkeypatch.setattr(
        auth,
        "verify_navidrome_credentials",
        lambda _settings, username, password, **_kwargs: (
            username in {"alice", "bob"} and password == "correct"
        ),
    )
    monkeypatch.setattr(auth, "sync_navidrome_starred_for_user", lambda *_args, **_kwargs: None)
    store = Store(db_path)
    store.init()
    return store


def _login(username: str) -> TestClient:
    client = TestClient(app)
    response = client.post("/api/v1/auth/login", json={"username": username, "password": "correct"})
    assert response.status_code == 200
    return client


def _user_store(store: Store, username: str) -> Store:
    return store.for_user(int(store.get_user_by_username(username)["id"]))


_PROFILE_PATHS = (
    "profile", "listens", "likes", "playlists", "top/artists", "top/releases", "top/tracks", "likes/tracks",
)


def test_api_profile_contract_and_case_insensitive_username(tmp_path: Path, monkeypatch):
    store = _init_auth_store(tmp_path, monkeypatch)
    _login("alice")
    bob = _login("bob")
    alice_store = _user_store(store, "alice")
    track = _track(store, tmp_path, "api-track", artist="Api Artist")
    _listen(store, alice_store, track, utc_now())

    response = bob.get("/api/v1/users/ALICE/profile", params={"period": "7d", "tz": "Europe/Moscow"})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "header", "period", "summary", "by_day_bucket", "by_day", "by_hour",
        "sound", "top_artists", "top_artists_total", "top_releases", "top_releases_total",
        "top_tracks", "top_tracks_total", "recent",
    }
    assert set(body["header"]) == {"username", "avatar", "created_at", "viewer_is_owner", "totals"}
    assert body["header"]["username"] == "alice"
    assert body["header"]["viewer_is_owner"] is False
    assert body["header"]["totals"] == {"listens": 1, "artists": 1, "likes": 0}
    assert body["period"]["key"] == "7d" and body["period"]["tz"] == "Europe/Moscow"
    assert len(body["by_day"]) == 7 and len(body["by_hour"]) == 24
    assert body["summary"]["listens"] == 1
    assert [item["id"] for item in body["recent"]] == [track]
    assert bob.get("/api/v1/users/alice/profile").json()["period"]["key"] == "30d"
    assert bob.get("/api/v1/users/alice/profile", params={"tz": "Bogus/Zone"}).json()["period"]["tz"] == "UTC"

    listens = bob.get("/api/v1/users/Alice/listens", params={"limit": 1}).json()
    assert listens["total"] == 1 and listens["items"][0]["id"] == track


def test_api_full_lists_of_profile_shelves(tmp_path: Path, monkeypatch):
    store = _init_auth_store(tmp_path, monkeypatch)
    _login("alice")
    bob = _login("bob")
    alice_store = _user_store(store, "alice")
    tracks = [_track(store, tmp_path, f"full{n}", artist=f"Full Artist {n}") for n in range(3)]
    for rank, track in enumerate(tracks):
        for _repeat in range(3 - rank):
            _listen(store, alice_store, track, utc_now())
        alice_store.set_track_liked(track, True)
    old = _track(store, tmp_path, "full-old", artist="Full Old")
    _listen(store, alice_store, old, (datetime.now(UTC) - timedelta(days=60)).isoformat())

    month = bob.get(
        "/api/v1/users/ALICE/top/artists", params={"period": "30d", "tz": "UTC", "limit": 2}
    ).json()
    year_releases = bob.get(
        "/api/v1/users/alice/top/releases", params={"period": "365d", "limit": 2, "offset": 2}
    ).json()
    likes = bob.get("/api/v1/users/alice/likes/tracks", params={"limit": 2, "offset": 2}).json()
    month_tracks = bob.get(
        "/api/v1/users/alice/top/tracks", params={"period": "30d", "tz": "UTC", "limit": 2, "offset": 1}
    ).json()

    assert [item["title"] for item in month["items"]] == ["Full Artist 0", "Full Artist 1"]
    assert (month["total"], month["limit"], month["offset"], month["next_offset"]) == (3, 2, 0, 2)
    assert month["period"]["key"] == "30d"
    assert year_releases["total"] == 4 and year_releases["next_offset"] is None
    assert [item["entity_id"] for item in year_releases["items"]] == [
        _release_of(store, tracks[2]),
        _release_of(store, old),
    ]
    assert likes["total"] == 3 and len(likes["items"]) == 1 and likes["next_offset"] is None
    # Tracks rank by listens like the profile's top tracks, with their counts.
    assert [(item["id"], item["listens"]) for item in month_tracks["items"]] == [(tracks[1], 2), (tracks[2], 1)]
    assert (month_tracks["total"], month_tracks["next_offset"]) == (3, None)
    assert bob.get("/api/v1/users/alice/top/labels").status_code == 422
    assert bob.get("/api/v1/users/alice/top/artists", params={"period": "14d"}).status_code == 422
    assert bob.get("/api/v1/users/alice/top/artists", params={"limit": 101}).status_code == 422
    assert bob.get("/api/v1/users/alice/likes/playlists").status_code == 422
    assert bob.get("/api/v1/users/alice/likes/artists", params={"offset": -1}).status_code == 422


def test_api_rejects_invalid_period_and_limits(tmp_path: Path, monkeypatch):
    _init_auth_store(tmp_path, monkeypatch)
    alice = _login("alice")

    assert alice.get("/api/v1/users/alice/profile", params={"period": "14d"}).status_code == 422
    assert alice.get("/api/v1/users/alice/listens", params={"limit": 101}).status_code == 422
    assert alice.get("/api/v1/users/alice/listens", params={"offset": -1}).status_code == 422
    assert alice.get("/api/v1/users/alice/likes", params={"limit": 0}).status_code == 422


def test_api_unknown_user_is_404_and_service_principal_is_403(tmp_path: Path, monkeypatch):
    _init_auth_store(tmp_path, monkeypatch)
    alice = _login("alice")
    service = TestClient(app)
    headers = {"X-Discocs-Service-Token": "svc-secret"}

    for path in _PROFILE_PATHS:
        assert alice.get(f"/api/v1/users/carol/{path}").status_code == 404, path
        assert service.get(f"/api/v1/users/alice/{path}", headers=headers).status_code == 403, path
        assert TestClient(app).get(f"/api/v1/users/alice/{path}").status_code == 401, path


def test_api_playlists_private_only_for_the_owner(tmp_path: Path, monkeypatch):
    store = _init_auth_store(tmp_path, monkeypatch)
    alice = _login("alice")
    bob = _login("bob")
    alice_store = _user_store(store, "alice")
    alice_store.create_playlist(title="Alice private")
    alice_store.create_playlist(title="Alice public", source={"visibility": "public"})
    _user_store(store, "bob").create_playlist(title="Bob public", source={"visibility": "public"})

    own = alice.get("/api/v1/users/alice/playlists").json()
    seen_by_bob = bob.get("/api/v1/users/alice/playlists").json()

    assert {item["title"] for item in own["items"]} == {"Alice private", "Alice public"}
    assert {item["title"] for item in seen_by_bob["items"]} == {"Alice public"}
    assert seen_by_bob["total"] == 1
    assert all(item["editable"] is True for item in own["items"])
    assert all(item["editable"] is False for item in seen_by_bob["items"])

    own_page = alice.get("/api/v1/users/alice/playlists", params={"limit": 1}).json()
    bob_page = bob.get("/api/v1/users/alice/playlists", params={"limit": 1, "offset": 1}).json()
    assert own_page["total"] == 2 and len(own_page["items"]) == 1 and own_page["next_offset"] == 1
    assert bob_page["total"] == 1 and bob_page["items"] == [] and bob_page["next_offset"] is None
    assert bob.get("/api/v1/users/alice/playlists", params={"limit": 0}).status_code == 422


def test_api_never_exposes_preferences_dislikes_or_settings(tmp_path: Path, monkeypatch):
    store = _init_auth_store(tmp_path, monkeypatch)
    _login("alice")
    bob = _login("bob")
    alice_store = _user_store(store, "alice")
    liked = _track(store, tmp_path, "loved")
    hated = _track(store, tmp_path, "hated")
    alice_store.set_track_liked(liked, True)
    alice_store.record_playback_event(PlaybackEventCreate(event_type="disliked", track_id=hated))
    alice_store.set_user_settings({"language": "ru", "transcoding_bitrate_kbps": 128})
    _listen(store, alice_store, liked, utc_now())
    _listen(store, alice_store, hated, utc_now())

    payloads = [bob.get(f"/api/v1/users/alice/{path}").json() for path in _PROFILE_PATHS]
    keys = set().union(*(_all_keys(payload) for payload in payloads))

    forbidden = {
        "score", "disliked", "dislikes", "play_count", "skip_count", "completion_count",
        "replay_count", "flow", "flow_profile", "settings", "language", "queue",
        "session", "sessions", "password", "navidrome_password", "token",
        "transcoding_bitrate_kbps", "transcoding_enabled", "prefetch_tracks",
        "prefetch_ahead_enabled",
    }
    assert keys.isdisjoint(forbidden), keys & forbidden
    likes = payloads[2]
    assert set(likes) == {"tracks", "releases", "artists", "limit", "offset"}
    assert [item["entity_id"] for item in likes["tracks"]["items"]] == [liked]
