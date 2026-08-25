"""Recognising a link as something the library already has.

Worth the lookup before every download: a library track has a real embedding
computed from the original file, so radio from it beats radio from a re-encoded
copy off YouTube — and nothing has to be fetched at all.

Matching is deliberately strict. A wrong match sends someone a different song
than the link they pasted, which is worse than one unnecessary download.
"""
from __future__ import annotations

import re
import unicodedata

# Video titles carry decoration a library tag never has.
NOISE_PATTERNS = (
    r"\(official[^)]*\)",
    r"\[official[^\]]*\]",
    r"\(lyric[^)]*\)",
    r"\(audio\)",
    r"\(video\)",
    r"\(hd\)",
    r"\(4k\)",
    r"\(remaster[^)]*\)",
    r"\[remaster[^\]]*\]",
    r"\(free download\)",
    r"\bfull album\b",
)
_NOISE = re.compile("|".join(NOISE_PATTERNS), re.IGNORECASE)
_FEATURING = re.compile(r"\b(feat|ft|featuring|with)\b.*$", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(value: str | None) -> str:
    """Comparable form: no decoration, no punctuation, no diacritics."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value).casefold()
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = _NOISE.sub(" ", text)
    text = _FEATURING.sub(" ", text)
    return _NON_ALNUM.sub("", text)


def is_confident_match(track, *, artist: str | None, title: str) -> bool:
    """Whether a library track is the same recording as the linked one.

    Titles must agree outright. Artists may be missing on the link side (many
    uploads only have a channel name), and then the title alone has to carry
    the match — so it must not be a bare word like "Intro".
    """
    linked_title = normalize(title)
    if not linked_title:
        return False
    if normalize(track.title) != linked_title:
        return False

    linked_artist = normalize(artist)
    if not linked_artist:
        return len(linked_title) >= 8

    track_artist = normalize(track.artist)
    return linked_artist == track_artist or linked_artist in track_artist or track_artist in linked_artist


def search_query(artist: str | None, title: str) -> str:
    return f"{artist} {title}".strip() if artist else title.strip()


def find_match(tracks, *, artist: str | None, title: str):
    """First confident match among search results, or None."""
    for track in tracks:
        if is_confident_match(track, artist=artist, title=title):
            return track
    return None
