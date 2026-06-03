from app.recommender import _same_album
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
