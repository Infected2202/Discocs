from bot.storage.user_prefs import (

    DEFAULT_AUDIO_PROFILE,

    delivery_prefs_from_profile,

    normalize_audio_profile,

)





def test_normalize_audio_profile():

    assert normalize_audio_profile("mp3:192") == "mp3:192"

    assert normalize_audio_profile("opus:256") == "opus:256"

    assert normalize_audio_profile("invalid") == DEFAULT_AUDIO_PROFILE

    assert normalize_audio_profile(None) == DEFAULT_AUDIO_PROFILE

    assert DEFAULT_AUDIO_PROFILE == "mp3:320"





def test_delivery_prefs_from_profile():

    prefs = delivery_prefs_from_profile("mp3:256")

    assert prefs.format == "mp3"

    assert prefs.bitrate == "256k"

    assert prefs.as_document is False

    assert prefs.file_extension == "mp3"

    assert prefs.cache_profile(with_cover=False) == "256k"

    assert prefs.cache_profile(with_cover=True) == "256k+cover-v2"



    opus = delivery_prefs_from_profile("opus:256")

    assert opus.format == "opus"

    assert opus.bitrate == "256k"

    assert opus.as_document is False

    assert opus.file_extension == "ogg"

    assert opus.cache_profile(with_cover=False) == "opus256k"

    assert opus.cache_profile(with_cover=True) == "opus256k+cover-v2"



    flac = delivery_prefs_from_profile("flac")

    assert flac.as_document is True

    assert flac.file_extension == "flac"

    assert flac.cache_profile(with_cover=True) == "flac+cover-v2"

