import time

from app.recommender import _ARTIST_KEY_SPLIT_RE, _artist_credit_keys, _same_album
from app.store import Track


def track(track_id: int, artist: str | None, album: str | None) -> Track:
    return Track(
        id=track_id,
        path=f"/music/{track_id}.flac",
        artist=artist,
        title="Title",
        album=album,
        duration=180.0,
        file_size=1,
        mtime=1,
    )


def test_same_album_requires_matching_album_and_artist_when_artist_present():
    assert _same_album(track(1, "A", "Album"), track(2, "A", "Album"))
    assert not _same_album(track(1, "A", "Album"), track(2, "B", "Album"))
    assert not _same_album(track(1, "A", None), track(2, "A", "Album"))


def test_artist_credit_keys_splits_collaborators():
    assert _artist_credit_keys("Alpha feat. Beta & Gamma") == ("alpha", "beta", "gamma")
    assert _artist_credit_keys("AT&T Band") == ("at&t band",)
    assert _artist_credit_keys("Solo Artist", count_collaboration_artists=False) == ("solo artist",)


def test_artist_credit_split_regex_handles_whitespace_flood_without_hanging():
    # Regression for S5852: the old `\s+...\s+` pattern let backtracking
    # split a long whitespace run in polynomially many ways once no
    # delimiter followed. _metadata_key() collapses whitespace before the
    # regex ever sees it in the normal call path, so this exercises the
    # compiled pattern directly to guard against it being reused elsewhere
    # (or the normalization step being removed) without this protection.
    pathological = "Artist" + " " * 50_000
    started = time.perf_counter()
    _ARTIST_KEY_SPLIT_RE.split(pathological)
    assert time.perf_counter() - started < 1.0
