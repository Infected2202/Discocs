from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any


UNKNOWN_ARTIST_NAME = "Unknown Artist"
UNKNOWN_RELEASE_TYPE = "unknown"
_NAVIDROME_URI_PREFIX = "navidrome://"
VALID_RELEASE_TYPES = {
    "album",
    "ep",
    "single",
    "compilation",
    "soundtrack",
    "mix",
    UNKNOWN_RELEASE_TYPE,
}


@dataclass(frozen=True)
class ArtistCredit:
    name: str
    role: str = "primary"
    position: int = 0
    credit_text: str | None = None
    confidence: str = "derived"


@dataclass(frozen=True)
class TrackMetadataEnvelope:
    title: str | None
    artist: str | None
    album: str | None
    album_artist: str | None = None
    genre: str | None = None
    year: int | None = None
    duration: float | None = None
    path: str | None = None
    track_number: int | None = None
    disc_number: int | None = None
    total_tracks: int | None = None
    release_type: str = UNKNOWN_RELEASE_TYPE
    release_date: str | None = None
    cover_art_id: str | None = None
    provider: str | None = None
    provider_track_id: str | None = None
    provider_release_id: str | None = None
    provider_artist_id: str | None = None
    raw_json: str | None = None


def normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().split()).casefold()


def clean_display_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).strip().split())
    return text or None


def parse_artist_credit(credit: str | None) -> list[ArtistCredit]:
    display = clean_display_text(credit)
    if not display:
        return [ArtistCredit(name=UNKNOWN_ARTIST_NAME, credit_text=credit, confidence="fallback")]

    pieces = _split_artist_credit(display)
    if not pieces:
        pieces = [display]
    seen: set[str] = set()
    artists: list[ArtistCredit] = []
    for piece in pieces:
        normalized = normalize_text(piece)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        artists.append(
            ArtistCredit(
                name=piece,
                role="primary",
                position=len(artists),
                credit_text=display,
                confidence="derived",
            )
        )
    return artists or [ArtistCredit(name=display, credit_text=display)]


def release_identity_key(envelope: TrackMetadataEnvelope) -> tuple[str, str]:
    provider = clean_display_text(envelope.provider)
    provider_release_id = clean_display_text(envelope.provider_release_id)
    if provider and provider_release_id:
        return f"provider:{normalize_text(provider)}:release:{provider_release_id}", "provider"

    album_title = clean_display_text(envelope.album)
    release_title = album_title or clean_display_text(envelope.title) or _path_stem(envelope.path)
    artist_key = normalize_text(envelope.album_artist or envelope.artist or UNKNOWN_ARTIST_NAME)
    title_key = normalize_text(release_title)
    folder_key = _release_folder_key(envelope.path, has_album=bool(album_title))
    if folder_key and title_key:
        if envelope.album_artist:
            return f"local-folder:{folder_key}:artist:{artist_key}:title:{title_key}", "derived"
        return f"local-folder:{folder_key}:title:{title_key}", "derived"

    if title_key:
        year = envelope.year if envelope.year is not None else ""
        return f"metadata:artist:{artist_key}:title:{title_key}:year:{year}", "derived"

    provider_track_id = clean_display_text(envelope.provider_track_id)
    if provider and provider_track_id:
        return f"provider:{normalize_text(provider)}:track:{provider_track_id}", "synthetic"

    path_key = normalize_text(envelope.path or "")
    return f"synthetic:path:{path_key or title_key or 'unknown'}", "synthetic"


def envelope_from_track_row(row: Any) -> TrackMetadataEnvelope:
    return TrackMetadataEnvelope(
        title=row["title"],
        artist=row["artist"],
        album=row["album"],
        genre=row["genre"] if "genre" in row.keys() else None,
        year=int(row["year"]) if "year" in row.keys() and row["year"] is not None else None,
        duration=float(row["duration"]) if row["duration"] is not None else None,
        path=str(row["path"]),
    )


def envelope_from_scanned_track(scanned: Any) -> TrackMetadataEnvelope:
    return TrackMetadataEnvelope(
        title=scanned.title,
        artist=scanned.artist,
        album=scanned.album,
        album_artist=getattr(scanned, "album_artist", None),
        genre=scanned.genre,
        year=scanned.year,
        duration=scanned.duration,
        path=str(scanned.path),
        track_number=getattr(scanned, "track_number", None),
        disc_number=getattr(scanned, "disc_number", None),
        total_tracks=getattr(scanned, "total_tracks", None),
    )


def envelope_from_navidrome_song(song: Any, raw_json: str | None = None) -> TrackMetadataEnvelope:
    raw = song.raw or {}
    album_id = _first_raw_value(raw, "albumId", "album_id")
    cover_art = _first_raw_value(raw, "coverArt", "coverArtId", "cover_art_id")
    album_artist = _first_raw_value(raw, "albumArtist", "albumartist", "album_artist")
    track_number = _optional_int(_first_raw_value(raw, "track", "trackNumber", "track_number"))
    disc_number = _optional_int(_first_raw_value(raw, "discNumber", "disc_number"))
    total_tracks = _optional_int(_first_raw_value(raw, "totalTracks", "trackTotal", "total_tracks"))
    release_type = _normalize_release_type(
        _first_raw_value(raw, "releaseType", "releaseTypes", "albumType", "mediaType")
    )
    release_date = clean_display_text(_first_raw_value(raw, "releaseDate", "date"))
    return TrackMetadataEnvelope(
        title=song.title,
        artist=song.artist,
        album=song.album,
        album_artist=album_artist,
        genre=song.genre,
        year=song.year,
        duration=float(song.duration) if song.duration is not None else None,
        path=f"navidrome://{song.id}",
        track_number=track_number,
        disc_number=disc_number,
        total_tracks=total_tracks,
        release_type=release_type,
        release_date=release_date,
        cover_art_id=cover_art,
        provider="navidrome",
        provider_track_id=song.id,
        provider_release_id=album_id,
        provider_artist_id=getattr(song, "artist_id", None) or _first_raw_value(raw, "artistId", "artist_id"),
        raw_json=raw_json,
    )


def release_title_for_envelope(envelope: TrackMetadataEnvelope) -> str:
    return (
        clean_display_text(envelope.album)
        or clean_display_text(envelope.title)
        or _path_stem(envelope.path)
        or "Unknown Release"
    )


def explicit_release_type(value: str | None) -> str:
    return _normalize_release_type(value)


# Delimiters that require whitespace around them use lookaround instead of
# `\s...\s` so the whitespace is only ever consumed once (by the outer
# `\s*`), not by both the outer wrapper and the alternative itself — that
# duplication is what made the old pattern polynomial on whitespace-heavy
# input (S5852).
_ARTIST_CREDIT_SPLIT_RE = re.compile(
    r"\s*(?:[;•]|(?<=\s)&(?=\s)|(?<=\s)feat\.(?=\s)|(?<=\s)ft\.(?=\s)|(?<=\s)featuring(?=\s))\s*",
    re.IGNORECASE,
)


def _split_artist_credit(value: str) -> list[str]:
    parts = [clean_display_text(part) for part in _ARTIST_CREDIT_SPLIT_RE.split(value)]
    return [part for part in parts if part]


def _release_folder_key(path: str | None, *, has_album: bool) -> str | None:
    if not path:
        return None
    try:
        parsed = Path(path)
    except (TypeError, ValueError):
        return None
    if str(path).startswith(_NAVIDROME_URI_PREFIX):
        return None
    parent = parsed.parent if has_album else parsed
    text = parent.as_posix() if parent != Path(".") else ""
    normalized = normalize_text(text)
    return normalized or None


def _path_stem(path: str | None) -> str | None:
    if not path:
        return None
    if str(path).startswith(_NAVIDROME_URI_PREFIX):
        return str(path).removeprefix(_NAVIDROME_URI_PREFIX) or None
    return clean_display_text(Path(path).stem)


def _first_raw_value(raw: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = raw.get(key)
        if value is None:
            continue
        if isinstance(value, dict):
            value = value.get("id") or value.get("name") or value.get("title")
        if isinstance(value, list):
            value = value[0] if value else None
        text = clean_display_text(str(value)) if value is not None else None
        if text:
            return text
    return None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None


def _normalize_release_type(value: str | None) -> str:
    normalized = normalize_text(value)
    aliases = {
        "album": "album",
        "ep": "ep",
        "single": "single",
        "compilation": "compilation",
        "soundtrack": "soundtrack",
        "mix": "mix",
    }
    return aliases.get(normalized, UNKNOWN_RELEASE_TYPE)


def stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)
