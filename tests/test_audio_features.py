import numpy as np

import app.audio_features as audio_features
from app.audio_features import AUDIO_FEATURE_EXTRACTOR, AudioFeatureAnalyzer


def test_audio_feature_analyzer_uses_feature_extractors(monkeypatch, tmp_path):
    monkeypatch.setattr(
        audio_features,
        "load_audio_with_ffmpeg",
        lambda path: np.ones(16000, dtype=np.float32),
    )
    monkeypatch.setattr(
        audio_features,
        "extract_rhythm_features",
        lambda audio: [
            audio_features.TrackFeature(
                name="bpm",
                value=128.0,
                unit="bpm",
                confidence=0.9,
                extractor=AUDIO_FEATURE_EXTRACTOR,
            )
        ],
    )
    monkeypatch.setattr(
        audio_features,
        "extract_key_features",
        lambda audio: [
            audio_features.TrackFeature(
                name="key",
                text_value="F#",
                confidence=0.7,
                extractor=AUDIO_FEATURE_EXTRACTOR,
            )
        ],
    )
    monkeypatch.setattr(audio_features, "extract_loudness_features", lambda audio: [])
    monkeypatch.setattr(audio_features, "extract_dynamic_features", lambda audio: [])

    features = AudioFeatureAnalyzer().analyze_track(tmp_path / "track.flac")

    assert [feature.name for feature in features] == ["bpm", "key"]
    assert features[0].value == 128.0
    assert features[1].text_value == "F#"
