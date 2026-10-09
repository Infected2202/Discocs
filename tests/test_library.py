import time

import pytest

from app.library import (
    _ARTIST_CREDIT_SPLIT_RE,
    TrackMetadataEnvelope,
    normalize_text,
    parse_artist_credit,
    parse_title_credits,
    release_identity_key,
    remixers_from_raw,
)


def test_normalize_text_collapses_casefolds_and_strips():
    assert normalize_text("  The   Artist  ") == "the artist"


def test_artist_credit_parser_splits_clear_separators():
    credits = parse_artist_credit("Alpha & Beta feat. Gamma")

    assert [credit.name for credit in credits] == ["Alpha", "Beta", "Gamma"]
    assert {credit.credit_text for credit in credits} == {"Alpha & Beta feat. Gamma"}


def test_artist_credit_parser_keeps_bare_ampersand_together():
    credits = parse_artist_credit("AT&T Band")
    assert [credit.name for credit in credits] == ["AT&T Band"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Alpha;Beta", ["Alpha", "Beta"]),
        ("Alpha\u2022Beta", ["Alpha", "Beta"]),
        ("Alpha & Beta", ["Alpha", "Beta"]),
        ("Alpha feat. Beta", ["Alpha", "Beta"]),
        ("Alpha ft. Beta", ["Alpha", "Beta"]),
        ("Alpha featuring Beta", ["Alpha", "Beta"]),
        ("AT&T Band", ["AT&T Band"]),
        ("Alpha& Beta", ["Alpha& Beta"]),
        ("Defeat. Victory", ["Defeat. Victory"]),
    ],
)
def test_artist_credit_parser_preserves_delimiter_rules(value, expected):
    assert [credit.name for credit in parse_artist_credit(value)] == expected


def test_artist_credit_split_regex_handles_whitespace_flood_without_hanging():
    # Regression for S5852: the old `\s*(?:...)\s*` pattern wrapped the
    # alternation in an unbounded quantifier, so re.split had to rescan the
    # remaining whitespace run from every position once no delimiter
    # followed — quadratic in the run length. clean_display_text() collapses
    # whitespace before the regex ever sees it in the normal call path, so
    # this exercises the compiled pattern directly to guard against it being
    # reused elsewhere (or the normalization step being removed) without
    # this protection.
    pathological = "Artist" + " " * 50_000
    started = time.perf_counter()
    _ARTIST_CREDIT_SPLIT_RE.split(pathological)
    assert time.perf_counter() - started < 1.0


def test_release_identity_prefers_provider_release_id():
    key, confidence = release_identity_key(
        TrackMetadataEnvelope(
            title="Track",
            artist="Artist",
            album="Album",
            provider="navidrome",
            provider_release_id="album-1",
        )
    )

    assert key == "provider:navidrome:release:album-1"
    assert confidence == "provider"


def test_release_identity_uses_path_aware_local_fallback(tmp_path):
    path = tmp_path / "Artist" / "Album" / "01 - Track.flac"
    envelope = TrackMetadataEnvelope(
        title="Track",
        artist="Artist",
        album="Album",
        path=str(path),
        year=2001,
    )

    key, confidence = release_identity_key(envelope)

    assert "local-folder:" in key
    assert "title:album" in key
    assert confidence == "derived"


def _title_credits(title):
    return [(credit.role, credit.text, credit.parts, credit.needs_known) for credit in parse_title_credits(title)]


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Pi Pu Pa (ft. RLGN)", [("featured", "RLGN", ("RLGN",), False)]),
        ("Track [feat. A & B]", [("featured", "A & B", ("A", "B"), False)]),
        ("Track (featuring Robyn)", [("featured", "Robyn", ("Robyn",), False)]),
        ("Noise feat. Lelah (Original Mix)", [("featured", "Lelah", ("Lelah",), False)]),
        ("Track (with Love)", [("featured", "Love", ("Love",), True)]),
        ("Track (Solomun Remix)", [("remixer", "Solomun", ("Solomun",), False)]),
        ("Track (Solomun Extended Remix)", [("remixer", "Solomun", ("Solomun",), False)]),
        ("Track (Solomun's Remix)", [("remixer", "Solomun", ("Solomun",), False)]),
        ("Track [Ray Keith & Nookie Remix]", [("remixer", "Ray Keith & Nookie", ("Ray Keith", "Nookie"), False)]),
        ("Track (M.A.X, Paolo Francesco Remix)", [("remixer", "M.A.X, Paolo Francesco", ("M.A.X", "Paolo Francesco"), False)]),
        ("Track (A x B Rework)", [("remixer", "A x B", ("A", "B"), False)]),
        ("Track (Remix by Coyu)", [("remixer", "Coyu", ("Coyu",), False)]),
        ("Track (Coyu Edit)", [("remixer", "Coyu", ("Coyu",), True)]),
        ("Track (Break Version)", [("remixer", "Break", ("Break",), True)]),
        ("Track - Coyu Remix", [("remixer", "Coyu", ("Coyu",), False)]),
        ("Track (Sorza’s Combined Remix)", [("remixer", "Sorza", ("Sorza",), False)]),
        (
            "Track (Two Armadillos “Rhythm of Life” Remix)",
            [("remixer", "Two Armadillos", ("Two Armadillos",), False)],
        ),
        (
            "Sadism (ft. Any Act) [Locked Club Remix]",
            [("featured", "Any Act", ("Any Act",), False), ("remixer", "Locked Club", ("Locked Club",), False)],
        ),
    ],
)
def test_title_credits_find_featured_artists_and_remixers(title, expected):
    assert _title_credits(title) == expected


@pytest.mark.parametrize(
    "title",
    [
        "Track (Original Mix)",
        "Track (Extended Mix)",
        "Track (Radio Edit)",
        "Track (Club Mix)",
        "Track (2008 Remix)",
        "Track (Original Short)",
        "Track (Unedited Version)",
        "Track (Sped Up Version)",
        "Track (Live)",
        "Track (Remix)",
        "Track (Deluxe Edition)",
        "Track - Radio Edit",
        "Defeat. Victory",
        "",
        None,
    ],
)
def test_title_credits_ignore_version_descriptions(title):
    assert parse_title_credits(title) == []


def test_title_credit_text_is_a_substring_of_the_title():
    title = "Tune (feat. MC Det) [Ray Keith & Nookie Remix]"
    for credit in parse_title_credits(title):
        assert credit.text in title
        assert all(part in title for part in credit.parts)


def test_title_credit_regexes_handle_whitespace_flood_without_hanging():
    pathological = "Track (" + " " * 50_000 + "Remix"
    started = time.perf_counter()
    parse_title_credits(pathological)
    parse_title_credits("Track feat." + " " * 50_000)
    assert time.perf_counter() - started < 1.0


def test_remixers_from_raw_reads_navidrome_contributors():
    raw = {
        "contributors": [
            {"role": "composer", "artist": {"name": "Locked Club"}},
            {"role": "remixer", "artist": {"name": "Coyu"}},
            {"role": "Remixer", "artist": {"name": "Coyu"}},
            {"role": "remixer"},
        ]
    }
    assert remixers_from_raw(raw) == ("Coyu",)
    assert remixers_from_raw({}) == ()
