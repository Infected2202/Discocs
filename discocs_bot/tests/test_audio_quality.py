from bot.storage.user_prefs import delivery_prefs_from_profile
from bot.utils.audio_quality import (
    send_as_document,
    should_send_original,
    should_use_navidrome_transcode,
    source_cache_profile,
)


def test_should_send_original_mp3_lower_than_target():
    prefs = delivery_prefs_from_profile("mp3:320")
    assert should_send_original("mp3", 192, prefs) is True
    assert should_send_original("mp3", 256, prefs) is True
    assert should_send_original("mp3", 320, prefs) is True
    assert should_send_original("mp3", 128, prefs) is True


def test_should_not_upscale_mp3():
    prefs = delivery_prefs_from_profile("mp3:192")
    assert should_send_original("mp3", 320, prefs) is False
    assert should_send_original("mp3", 256, prefs) is False


def test_lossless_source_never_sent_as_original_for_mp3_target():
    prefs = delivery_prefs_from_profile("mp3:320")
    assert should_send_original("flac", None, prefs) is False
    assert should_send_original("flac", 1000, prefs) is False


def test_flac_setting_accepts_lossy_original():
    prefs = delivery_prefs_from_profile("flac")
    assert should_send_original("mp3", 192, prefs) is True
    assert should_send_original("flac", None, prefs) is True
    assert should_send_original("alac", None, prefs) is True


def test_navidrome_transcode_only_when_needed():
    prefs = delivery_prefs_from_profile("mp3:320")
    assert (
        should_use_navidrome_transcode("flac", None, prefs, enabled=True) is True
    )
    assert (
        should_use_navidrome_transcode("mp3", 192, prefs, enabled=True) is False
    )
    assert (
        should_use_navidrome_transcode("ogg", 128, prefs, enabled=True) is False
    )
    assert (
        should_use_navidrome_transcode("ogg", 256, prefs, enabled=True) is False
    )


def test_navidrome_stream_bitrate_in_kbps():
    from unittest.mock import MagicMock

    from bot.services.delivery import DeliveryService

    settings = MagicMock()
    settings.navidrome_stream_max_bitrate = 320
    delivery = DeliveryService(settings, MagicMock(), MagicMock(), MagicMock())
    assert delivery._navidrome_stream_bitrate(delivery_prefs_from_profile("mp3:320")) == 320
    assert delivery._navidrome_stream_bitrate(delivery_prefs_from_profile("mp3:192")) == 192
    assert delivery._navidrome_stream_bitrate(delivery_prefs_from_profile("flac")) == 320


def test_opus_send_original_only_for_ogg_at_or_below_target():
    prefs = delivery_prefs_from_profile("opus:256")
    assert should_send_original("ogg", 256, prefs) is True
    assert should_send_original("ogg", 192, prefs) is True
    assert should_send_original("ogg", 320, prefs) is False
    assert should_send_original("mp3", 192, prefs) is False
    assert should_send_original("flac", 900, prefs) is False


def test_opus_does_not_use_navidrome_transcode():
    prefs = delivery_prefs_from_profile("opus:256")
    assert should_use_navidrome_transcode("flac", None, prefs, enabled=True) is False
    assert should_use_navidrome_transcode("mp3", 320, prefs, enabled=True) is False


def test_source_cache_profile():
    assert source_cache_profile("mp3", 192) == "192k"
    assert source_cache_profile("flac", None) == "source:flac"
    assert send_as_document("mp3") is False
    assert send_as_document("ogg") is False
    assert send_as_document("flac") is True
