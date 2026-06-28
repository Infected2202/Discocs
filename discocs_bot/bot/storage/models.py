from dataclasses import dataclass
from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(slots=True)
class Track:
    id: str
    title: str
    artist: str
    album: str
    year: int | None = None
    duration: int | None = None
    suffix: str | None = None
    cover_art_id: str | None = None
    album_id: str | None = None
    track_number: int | None = None
    bitrate_kbps: int | None = None

    @property
    def display_line(self) -> str:
        return f"{self.artist} — {self.title}"

    @property
    def subtitle(self) -> str:
        year = f" · {self.year}" if self.year else ""
        return f"{self.album}{year}"


@dataclass(slots=True)
class SimilarTrack:
    song_id: str
    score: float
    track: Track | None = None


@dataclass(slots=True)
class Album:
    id: str
    title: str
    artist: str
    year: int | None = None
    cover_art_id: str | None = None
    track_count: int = 0
