import time

from app.library import (
    _ARTIST_CREDIT_SPLIT_RE,
    TrackMetadataEnvelope,
    normalize_text,
    parse_artist_credit,
    release_identity_key,
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


def test_artist_credit_split_regex_handles_whitespace_flood_without_hanging():
    # Regression for S5852: the old `\s...\s` pattern let backtracking split
    # a long whitespace run in polynomially many ways once no delimiter
    # followed. clean_display_text() collapses whitespace before the regex
    # ever sees it in the normal call path, so this exercises the compiled
    # pattern directly to guard against it being reused elsewhere (or the
    # normalization step being removed) without this protection.
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
