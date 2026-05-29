from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from app.embedder import configure_tensorflow_logging, load_audio_with_ffmpeg
from app.store import TrackFeature


AUDIO_FEATURE_EXTRACTOR = "audio_features_v1"
logger = logging.getLogger(__name__)


class AudioFeatureAnalyzer:
    def analyze_track(self, path: Path) -> list[TrackFeature]:
        configure_tensorflow_logging()
        logger.info("Analyzing audio features path=%s extractor=%s", path, AUDIO_FEATURE_EXTRACTOR)
        audio = load_audio_with_ffmpeg(path)
        features: list[TrackFeature] = []
        features.extend(extract_rhythm_features(audio))
        features.extend(extract_key_features(audio))
        features.extend(extract_loudness_features(audio))
        features.extend(extract_dynamic_features(audio))
        return features


def extract_rhythm_features(audio: np.ndarray) -> list[TrackFeature]:
    try:
        from essentia.standard import RhythmExtractor2013
    except ImportError as exc:
        raise RuntimeError("essentia-tensorflow is required for rhythm extraction") from exc
    bpm, _beats, beats_confidence, _estimates, _intervals = RhythmExtractor2013(
        method="multifeature"
    )(audio)
    return [
        TrackFeature(
            name="bpm",
            value=float(bpm),
            unit="bpm",
            confidence=float(beats_confidence),
            extractor=AUDIO_FEATURE_EXTRACTOR,
        ),
    ]


def extract_key_features(audio: np.ndarray) -> list[TrackFeature]:
    try:
        from essentia.standard import KeyExtractor
    except ImportError as exc:
        raise RuntimeError("essentia-tensorflow is required for key extraction") from exc
    key, scale, strength = KeyExtractor(sampleRate=16000)(audio)
    return [
        TrackFeature(
            name="key",
            text_value=str(key),
            confidence=float(strength),
            extractor=AUDIO_FEATURE_EXTRACTOR,
        ),
        TrackFeature(
            name="scale",
            text_value=str(scale),
            confidence=float(strength),
            extractor=AUDIO_FEATURE_EXTRACTOR,
        ),
        TrackFeature(
            name="key_strength",
            value=float(strength),
            extractor=AUDIO_FEATURE_EXTRACTOR,
        ),
    ]


def extract_loudness_features(audio: np.ndarray) -> list[TrackFeature]:
    try:
        from essentia.standard import LoudnessEBUR128
    except ImportError as exc:
        raise RuntimeError("essentia-tensorflow is required for loudness extraction") from exc
    result = LoudnessEBUR128(sampleRate=16000)(audio)
    values = list(result) if isinstance(result, tuple) else [result]
    features: list[TrackFeature] = []
    if len(values) >= 3:
        features.append(
            TrackFeature(
                name="loudness_integrated",
                value=float(values[2]),
                unit="LUFS",
                extractor=AUDIO_FEATURE_EXTRACTOR,
            )
        )
    if len(values) >= 4:
        features.append(
            TrackFeature(
                name="loudness_range",
                value=float(values[3]),
                unit="LU",
                extractor=AUDIO_FEATURE_EXTRACTOR,
            )
        )
    return features


def extract_dynamic_features(audio: np.ndarray) -> list[TrackFeature]:
    try:
        from essentia.standard import DynamicComplexity
    except ImportError as exc:
        raise RuntimeError("essentia-tensorflow is required for dynamic extraction") from exc
    dynamic_complexity, loudness = DynamicComplexity()(audio)
    return [
        TrackFeature(
            name="dynamic_complexity",
            value=float(dynamic_complexity),
            extractor=AUDIO_FEATURE_EXTRACTOR,
        ),
        TrackFeature(
            name="dynamic_loudness",
            value=float(loudness),
            extractor=AUDIO_FEATURE_EXTRACTOR,
        ),
    ]
