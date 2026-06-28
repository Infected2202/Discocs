from app.library import (
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
