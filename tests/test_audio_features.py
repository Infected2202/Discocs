import numpy as np
import sys
import types

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


def test_loudness_extractor_converts_mono_audio_to_stereo(monkeypatch):
    calls = []

    class FakeLoudnessEBUR128:
        def __init__(self, sampleRate):
            assert sampleRate == 16000

        def __call__(self, audio):
            calls.append(audio)
            return ([], [], -14.5, 6.0)

    essentia_module = types.ModuleType("essentia")
    standard_module = types.ModuleType("essentia.standard")
    standard_module.LoudnessEBUR128 = FakeLoudnessEBUR128
    monkeypatch.setitem(sys.modules, "essentia", essentia_module)
    monkeypatch.setitem(sys.modules, "essentia.standard", standard_module)

    features = audio_features.extract_loudness_features(
        np.array([0.1, -0.2, 0.3], dtype=np.float32)
    )

    assert calls[0].shape == (3, 2)
    assert np.allclose(calls[0][:, 0], calls[0][:, 1])
    assert [feature.name for feature in features] == [
        "loudness_integrated",
        "loudness_range",
    ]
