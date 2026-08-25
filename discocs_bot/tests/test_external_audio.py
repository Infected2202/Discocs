"""Metadata mapping, quality decisions, and the media cache for link downloads."""
from __future__ import annotations

import os
import time
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.pop("bot", None)

from bot.services.external_audio import (
    ExternalAudioError,
    LinkAudioService,
    clean_uploader,
    info_from_payload,
    media_key_for,
    split_artist_title,
)
from bot.services.media_cache import MediaCache, safe_stem
from bot.utils.external_quality import (
    FALLBACK_BITRATE_KBPS,
    estimate_bitrate_kbps,
    part_count,
    part_ranges,
    part_title,
    target_bitrate_kbps,
)


def payload(**overrides) -> dict:
    base = {
        "id": "dQw4w9WgXcQ",
        "extractor_key": "Youtube",
        "title": "Aphex Twin - Xtal",
        "uploader": "Warp Records",
        "duration": 293,
        "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hq.jpg",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def test_info_splits_artist_and_title_from_the_video_title():
    info = info_from_payload(payload())

    assert info.artist == "Aphex Twin"
    assert info.title == "Xtal"
    assert info.source == "youtube"
    assert info.url_key == "youtube:dQw4w9WgXcQ"
    assert info.media_key == media_key_for("youtube:dQw4w9WgXcQ")
    assert info.duration == 293


def test_info_prefers_music_metadata_over_the_video_title():
    info = info_from_payload(payload(artist="Boards of Canada", track="Roygbiv"))

    assert info.artist == "Boards of Canada"
    assert info.title == "Roygbiv"


def test_info_falls_back_to_the_uploader_when_the_title_has_no_separator():
    info = info_from_payload(payload(title="Live set at Dekmantel", uploader="Antal - Topic"))

    assert info.artist == "Antal"
    assert info.title == "Live set at Dekmantel"


def test_playlists_are_refused():
    with pytest.raises(ExternalAudioError):
        info_from_payload(payload(_type="playlist"))


def test_live_streams_are_refused():
    with pytest.raises(ExternalAudioError):
        info_from_payload(payload(is_live=True))


def test_payload_without_an_id_is_refused():
    with pytest.raises(ExternalAudioError):
        info_from_payload(payload(id=""))


def test_clean_uploader_strips_the_youtube_topic_suffix():
    assert clean_uploader("Aphex Twin - Topic") == "Aphex Twin"
    assert clean_uploader("") is None


def test_split_artist_title_handles_dash_variants():
    assert split_artist_title("A — B", None) == ("A", "B")
    assert split_artist_title("A – B", None) == ("A", "B")
    assert split_artist_title("Just a title", "Chan") == ("Chan", "Just a title")


def test_media_key_is_short_enough_for_callback_data():
    key = media_key_for("youtube:" + "x" * 500)

    assert len(f"extget:{key}".encode()) <= 64


# ---------------------------------------------------------------------------
# Duration gate
# ---------------------------------------------------------------------------

def settings(**overrides) -> SimpleNamespace:
    base = {
        "ytdlp_cookies_file": "",
        "external_max_duration_minutes": 180,
        "external_max_download_bytes": 500 * 1024 * 1024,
        "external_bitrate_headroom": 1.0,
        "external_max_bitrate_kbps": 320,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_service_refuses_audio_longer_than_the_limit():
    service = LinkAudioService(settings(external_max_duration_minutes=60))
    info = info_from_payload(payload(duration=7200))

    with pytest.raises(ExternalAudioError):
        service._check_duration(info)


def test_service_accepts_audio_within_the_limit():
    service = LinkAudioService(settings(external_max_duration_minutes=60))

    service._check_duration(info_from_payload(payload(duration=600)))


def test_generic_extractor_is_excluded_from_the_download_options():
    service = LinkAudioService(settings())

    assert service._base_options()["allowed_extractors"] == ["default", "-generic"]
    assert service._base_options()["noplaylist"] is True


def test_cookie_file_is_only_passed_when_configured():
    assert "cookiefile" not in LinkAudioService(settings())._base_options()

    configured = LinkAudioService(settings(ytdlp_cookies_file="/data/cookies.txt"))

    assert configured._base_options()["cookiefile"] == "/data/cookies.txt"


def test_downloaded_path_ignores_the_thumbnail(tmp_path: Path):
    (tmp_path / "key123.jpg").write_bytes(b"thumb")
    audio = tmp_path / "key123.opus"
    audio.write_bytes(b"audio")

    resolved = LinkAudioService._downloaded_path({}, tmp_path, "key123")

    assert resolved == audio
    assert LinkAudioService.thumbnail_path(tmp_path, "key123") == tmp_path / "key123.jpg"


# ---------------------------------------------------------------------------
# Quality and splitting
# ---------------------------------------------------------------------------

def test_target_bitrate_never_exceeds_the_source():
    assert target_bitrate_kbps(128) == 128
    assert target_bitrate_kbps(160) == 160
    assert target_bitrate_kbps(700) == 320


def test_target_bitrate_applies_configured_headroom():
    assert target_bitrate_kbps(128, headroom=1.5) == 192
    # Headroom below 1.0 must not quietly downgrade the audio.
    assert target_bitrate_kbps(128, headroom=0.5) == 128


def test_target_bitrate_falls_back_when_the_source_is_unknown():
    assert target_bitrate_kbps(None) == FALLBACK_BITRATE_KBPS


def test_estimate_bitrate_from_size_and_duration():
    assert estimate_bitrate_kbps(2 * 1024 * 1024, 120) == 139
    assert estimate_bitrate_kbps(None, 120) is None
    assert estimate_bitrate_kbps(1000, 0) is None


def test_part_count_keeps_every_part_under_the_limit():
    limit = 50 * 1024 * 1024

    assert part_count(10 * 1024 * 1024, limit) == 1
    assert part_count(limit, limit) == 1

    size = 140 * 1024 * 1024
    count = part_count(size, limit)

    assert count == 3
    assert size / count < limit


def test_part_ranges_cover_the_whole_file():
    ranges = part_ranges(300.0, 3)

    assert len(ranges) == 3
    assert ranges[0] == (0.0, 100.0)
    assert ranges[-1][0] + ranges[-1][1] == 300.0


def test_part_title_numbers_multipart_uploads_only():
    assert part_title("Xtal", 0, 1) == "Xtal"
    assert part_title("Xtal", 1, 3) == "Xtal (2/3)"


# ---------------------------------------------------------------------------
# Media cache
# ---------------------------------------------------------------------------

def test_safe_stem_strips_path_separators():
    assert safe_stem("../../etc/passwd") == "etc_passwd"
    assert safe_stem("youtube:abc") == "youtube_abc"
    assert safe_stem("///") == "media"


def test_cache_finds_audio_and_ignores_thumbnails(tmp_path: Path):
    cache = MediaCache(tmp_path, max_bytes=1024)
    (tmp_path / "key1.jpg").write_bytes(b"thumb")
    audio = tmp_path / "key1.opus"
    audio.write_bytes(b"audio")

    assert cache.find("key1") == audio
    assert cache.find("missing") is None


def test_cache_trims_least_recently_used_files_first(tmp_path: Path):
    cache = MediaCache(tmp_path, max_bytes=200)
    old = tmp_path / "old.mp3"
    fresh = tmp_path / "fresh.mp3"
    old.write_bytes(b"x" * 150)
    fresh.write_bytes(b"x" * 150)
    past = time.time() - 3600
    os.utime(old, (past, past))

    removed = cache.trim()

    assert removed == [old]
    assert not old.exists()
    assert fresh.exists()


def test_cache_keeps_everything_while_under_the_limit(tmp_path: Path):
    cache = MediaCache(tmp_path, max_bytes=10_000)
    (tmp_path / "a.mp3").write_bytes(b"x" * 100)

    assert cache.trim() == []
    assert cache.total_bytes() == 100
