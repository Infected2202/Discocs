import numpy as np
import sys
import types

import app.audio_features as audio_features
from app.audio_features import AUDIO_FEATURE_EXTRACTOR, AudioFeatureAnalyzer


def test_audio_feature_analyzer_uses_feature_extractors(monkeypatch, tmp_path):
    audio_16k = np.ones(16000, dtype=np.float32)
    audio_44k = np.ones(44100, dtype=np.float32) * 2
    calls = []

    def fake_load_audio(path, sample_rate=16000):
        calls.append(sample_rate)
        if sample_rate == audio_features.ESSENTIA_RHYTHM_SAMPLE_RATE:
            return audio_44k
        return audio_16k

    monkeypatch.setattr(
        audio_features,
        "load_audio_with_ffmpeg",
        fake_load_audio,
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
        ] if audio is audio_44k else [],
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
        ] if audio is audio_16k else [],
    )
    monkeypatch.setattr(audio_features, "extract_loudness_features", lambda audio: [])
    monkeypatch.setattr(
        audio_features,
        "extract_dynamic_features",
        lambda audio: [] if audio is audio_44k else [
            audio_features.TrackFeature(
                name="unexpected",
                value=1.0,
                extractor=AUDIO_FEATURE_EXTRACTOR,
            )
        ],
    )

    features = AudioFeatureAnalyzer().analyze_track(tmp_path / "track.flac")

    assert calls == [
        audio_features.EMBEDDING_SAMPLE_RATE,
        audio_features.ESSENTIA_RHYTHM_SAMPLE_RATE,
    ]
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


def test_rhythm_extractor_truncates_long_audio_to_avoid_buffer_overflow(monkeypatch):
    calls = []

    class FakeRhythmExtractor2013:
        def __init__(self, method):
            assert method == "multifeature"

        def __call__(self, audio):
            calls.append(audio)
            return (120.0, [], 0.8, [], [])

    essentia_module = types.ModuleType("essentia")
    standard_module = types.ModuleType("essentia.standard")
    standard_module.RhythmExtractor2013 = FakeRhythmExtractor2013
    monkeypatch.setitem(sys.modules, "essentia", essentia_module)
    monkeypatch.setitem(sys.modules, "essentia.standard", standard_module)

    max_samples = (
        audio_features.RHYTHM_MAX_DURATION_SECONDS * audio_features.ESSENTIA_RHYTHM_SAMPLE_RATE
    )
    long_audio = np.ones(max_samples + 1000, dtype=np.float32)

    features = audio_features.extract_rhythm_features(long_audio)

    assert len(calls[0]) == max_samples
    assert features[0].value == 120.0


def test_rhythm_extractor_keeps_short_audio_untouched(monkeypatch):
    calls = []

    class FakeRhythmExtractor2013:
        def __init__(self, method):
            assert method == "multifeature"

        def __call__(self, audio):
            calls.append(audio)
            return (95.0, [], 0.5, [], [])

    essentia_module = types.ModuleType("essentia")
    standard_module = types.ModuleType("essentia.standard")
    standard_module.RhythmExtractor2013 = FakeRhythmExtractor2013
    monkeypatch.setitem(sys.modules, "essentia", essentia_module)
    monkeypatch.setitem(sys.modules, "essentia.standard", standard_module)

    short_audio = np.ones(44100 * 30, dtype=np.float32)

    audio_features.extract_rhythm_features(short_audio)

    assert calls[0] is short_audio


def test_dynamic_extractor_uses_essentia_rhythm_sample_rate(monkeypatch):
    calls = []

    class FakeDynamicComplexity:
        def __init__(self, sampleRate, frameSize):
            calls.append((sampleRate, frameSize))

        def __call__(self, audio):
            return (12.5, -18.0)

    essentia_module = types.ModuleType("essentia")
    standard_module = types.ModuleType("essentia.standard")
    standard_module.DynamicComplexity = FakeDynamicComplexity
    monkeypatch.setitem(sys.modules, "essentia", essentia_module)
    monkeypatch.setitem(sys.modules, "essentia.standard", standard_module)

    features = audio_features.extract_dynamic_features(np.array([0.1, -0.2], dtype=np.float32))

    assert calls == [(audio_features.ESSENTIA_RHYTHM_SAMPLE_RATE, 0.2)]
    assert [feature.name for feature in features] == [
        "dynamic_complexity",
        "dynamic_loudness",
    ]
    assert features[0].value == 12.5
