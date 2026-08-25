"""Recognising a linked track as one the library already has."""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.pop("bot", None)

from bot.storage.models import Track
from bot.utils.library_match import find_match, is_confident_match, normalize, search_query


def track(title: str, artist: str = "Aphex Twin") -> Track:
    return Track(id="1", title=title, artist=artist, album="Selected Ambient Works")


def test_normalize_strips_decoration_and_punctuation():
    assert normalize("Xtal (Official Video)") == "xtal"
    assert normalize("Xtal [Remastered 2021]") == "xtal"
    assert normalize("Windowlicker — Aphex Twin") == "windowlickeraphextwin"
    assert normalize("Björk") == "bjork"
    assert normalize(None) == ""


def test_featuring_suffix_is_dropped():
    assert normalize("Runaway feat. Pusha T") == "runaway"
    assert normalize("Runaway (ft. Pusha T)") == "runaway"


def test_same_recording_matches_through_video_decoration():
    assert is_confident_match(track("Xtal"), artist="Aphex Twin", title="Xtal (Official Video)")


def test_artist_variants_still_match():
    assert is_confident_match(track("Xtal", artist="Aphex Twin"), artist="aphex twin", title="Xtal")
    assert is_confident_match(
        track("Xtal", artist="Aphex Twin & Friends"), artist="Aphex Twin", title="Xtal"
    )


def test_different_song_does_not_match():
    assert not is_confident_match(track("Ageispolis"), artist="Aphex Twin", title="Xtal")


def test_different_artist_does_not_match():
    # A cover is a different recording, and radio from it would be wrong.
    assert not is_confident_match(track("Xtal", artist="Some Coverband"), artist="Aphex Twin", title="Xtal")


def test_short_title_without_an_artist_is_not_enough():
    # "Intro" exists on half the albums ever made.
    assert not is_confident_match(track("Intro", artist="Whoever"), artist=None, title="Intro")


def test_long_title_without_an_artist_can_carry_the_match():
    assert is_confident_match(
        track("Come to Daddy", artist="Aphex Twin"),
        artist=None,
        title="Come to Daddy",
    )


def test_find_match_returns_the_first_confident_result():
    results = [track("Ageispolis"), track("Xtal"), track("Tha")]

    assert find_match(results, artist="Aphex Twin", title="Xtal").title == "Xtal"
    assert find_match(results, artist="Aphex Twin", title="Nothing Here") is None


def test_search_query_uses_the_artist_when_there_is_one():
    assert search_query("Aphex Twin", "Xtal") == "Aphex Twin Xtal"
    assert search_query(None, "Xtal") == "Xtal"
