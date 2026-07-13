from __future__ import annotations

import logging
from pathlib import Path
from time import perf_counter

import numpy as np

from app.embedder import configure_tensorflow_logging, load_audio_with_ffmpeg
from app.store import TrackFeature


AUDIO_FEATURE_EXTRACTOR = "audio_features_v1"
EMBEDDING_SAMPLE_RATE = 16000
ESSENTIA_RHYTHM_SAMPLE_RATE = 44100
# RhythmExtractor2013's OnsetDetectionGlobal step has a fixed-size internal
# output buffer and raises "output buffer is full" on very long tracks (DJ
# mixes, podcasts mistagged as a single track). BPM is stable early on, so a
# representative prefix is enough — cap the input instead of failing the task.
RHYTHM_MAX_DURATION_SECONDS = 1800
logger = logging.getLogger(__name__)


class AudioFeatureAnalyzer:
    def analyze_track(self, path: Path) -> list[TrackFeature]:
        configure_tensorflow_logging()
        logger.info("Analyzing audio features path=%s extractor=%s", path, AUDIO_FEATURE_EXTRACTOR)
        total_started = perf_counter()
        stage_started = perf_counter()
        audio = load_audio_with_ffmpeg(path, sample_rate=EMBEDDING_SAMPLE_RATE)
        decode_16k_seconds = perf_counter() - stage_started

        stage_started = perf_counter()
        rhythm_audio = load_audio_with_ffmpeg(path, sample_rate=ESSENTIA_RHYTHM_SAMPLE_RATE)
        decode_44k_seconds = perf_counter() - stage_started

        features: list[TrackFeature] = []
        stage_started = perf_counter()
        features.extend(extract_rhythm_features(rhythm_audio))
        rhythm_seconds = perf_counter() - stage_started

        stage_started = perf_counter()
        features.extend(extract_key_features(audio))
        key_seconds = perf_counter() - stage_started

        stage_started = perf_counter()
        features.extend(extract_loudness_features(audio))
        loudness_seconds = perf_counter() - stage_started

        stage_started = perf_counter()
        features.extend(extract_dynamic_features(rhythm_audio))
        dynamic_seconds = perf_counter() - stage_started

        total_seconds = perf_counter() - total_started
        logger.info(
            "Audio feature timing path=%s extractor=%s total_seconds=%.3f "
            "decode_16k_seconds=%.3f decode_44k_seconds=%.3f rhythm_seconds=%.3f "
            "key_seconds=%.3f loudness_seconds=%.3f dynamic_seconds=%.3f "
            "audio_16k_samples=%s audio_44k_samples=%s",
            path,
            AUDIO_FEATURE_EXTRACTOR,
            total_seconds,
            decode_16k_seconds,
            decode_44k_seconds,
            rhythm_seconds,
            key_seconds,
            loudness_seconds,
            dynamic_seconds,
            int(audio.size),
            int(rhythm_audio.size),
        )
        return features


def extract_rhythm_features(audio: np.ndarray) -> list[TrackFeature]:
    try:
        from essentia.standard import RhythmExtractor2013
    except ImportError as exc:
        raise RuntimeError("essentia-tensorflow is required for rhythm extraction") from exc
    max_samples = RHYTHM_MAX_DURATION_SECONDS * ESSENTIA_RHYTHM_SAMPLE_RATE
    rhythm_audio = audio[:max_samples] if audio.size > max_samples else audio
    bpm, _beats, beats_confidence, _estimates, _intervals = RhythmExtractor2013(
        method="multifeature"
    )(rhythm_audio)
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
    result = LoudnessEBUR128(sampleRate=16000)(mono_to_stereo(audio))
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


def mono_to_stereo(audio: np.ndarray) -> np.ndarray:
    mono = np.asarray(audio, dtype=np.float32).reshape(-1)
    return np.column_stack((mono, mono)).astype(np.float32, copy=False)


def extract_dynamic_features(audio: np.ndarray) -> list[TrackFeature]:
    try:
        from essentia.standard import DynamicComplexity
    except ImportError as exc:
        raise RuntimeError("essentia-tensorflow is required for dynamic extraction") from exc
    dynamic_complexity, loudness = DynamicComplexity(
        sampleRate=ESSENTIA_RHYTHM_SAMPLE_RATE,
        frameSize=0.2,
    )(audio)
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
