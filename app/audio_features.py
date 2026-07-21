from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import numpy as np

from app.embedder import configure_tensorflow_logging, load_audio_with_ffmpeg
from app.store import TrackFeature


AUDIO_FEATURE_EXTRACTOR = "audio_features_v2"
LEGACY_AUDIO_FEATURE_EXTRACTOR = "audio_features_v1"
EMBEDDING_SAMPLE_RATE = 16000
ESSENTIA_RHYTHM_SAMPLE_RATE = 44100
# RhythmExtractor2013's OnsetDetectionGlobal step has a fixed-size internal
# output buffer and raises "output buffer is full" on very long tracks (DJ
# mixes, podcasts mistagged as a single track). BPM is stable early on, so a
# representative prefix is enough — cap the input instead of failing the task.
RHYTHM_MAX_DURATION_SECONDS = 1800
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RhythmAnalysis:
    bpm: float
    beats: np.ndarray
    confidence: float
    estimates: np.ndarray
    intervals: np.ndarray


@dataclass(frozen=True)
class AudioFeatureAnalysis:
    features: list[TrackFeature]
    timeline_manifest: dict[str, object]
    timeline_payload: bytes
    timings: dict[str, float] | None = None


class AudioFeatureAnalyzer:
    def analyze_bundle(
        self,
        path: Path,
        *,
        track_id: int,
        source: dict[str, object],
    ) -> AudioFeatureAnalysis:
        """Compute scalar features and the browser timeline in one analysis task."""
        from app.timeline.extractor import encode_audio_timeline

        configure_tensorflow_logging()
        logger.info("Analyzing audio bundle path=%s extractor=%s", path, AUDIO_FEATURE_EXTRACTOR)
        started = perf_counter()
        rhythm_audio = load_audio_with_ffmpeg(path, sample_rate=ESSENTIA_RHYTHM_SAMPLE_RATE)
        decoded_at = perf_counter()
        audio = resample_audio(
            rhythm_audio,
            input_sample_rate=ESSENTIA_RHYTHM_SAMPLE_RATE,
            output_sample_rate=EMBEDDING_SAMPLE_RATE,
        )
        resampled_at = perf_counter()
        rhythm = analyze_rhythm(rhythm_audio)
        rhythm_at = perf_counter()
        key_features = extract_key_features(audio)
        key_at = perf_counter()
        loudness_features = extract_loudness_features(audio)
        del audio
        loudness_at = perf_counter()
        dynamic_features = extract_dynamic_features(rhythm_audio)
        dynamic_at = perf_counter()
        features = [
            TrackFeature(
                name="bpm",
                value=rhythm.bpm,
                unit="bpm",
                confidence=rhythm.confidence,
                extractor=AUDIO_FEATURE_EXTRACTOR,
            ),
            *key_features,
            *loudness_features,
            *dynamic_features,
        ]
        manifest, payload = encode_audio_timeline(
            rhythm_audio,
            track_id=track_id,
            source=source,
            rhythm=rhythm,
        )
        finished_at = perf_counter()
        return AudioFeatureAnalysis(
            features=features,
            timeline_manifest=manifest,
            timeline_payload=payload,
            timings={
                "decode": decoded_at - started,
                "resample": resampled_at - decoded_at,
                "rhythm": rhythm_at - resampled_at,
                "key": key_at - rhythm_at,
                "loudness": loudness_at - key_at,
                "dynamic": dynamic_at - loudness_at,
                "timeline": finished_at - dynamic_at,
                "total": finished_at - started,
            },
        )


def resample_audio(
    audio: np.ndarray,
    *,
    input_sample_rate: int,
    output_sample_rate: int,
) -> np.ndarray:
    """Derive the feature-rate PCM from the shared high-rate decode."""
    try:
        from essentia.standard import Resample
    except ImportError as exc:
        raise RuntimeError("essentia-tensorflow is required for audio resampling") from exc
    resampled = Resample(
        inputSampleRate=input_sample_rate,
        outputSampleRate=output_sample_rate,
        quality=1,
    )(np.asarray(audio, dtype=np.float32))
    return np.asarray(resampled, dtype=np.float32)


def analyze_rhythm(audio: np.ndarray) -> RhythmAnalysis:
    """Return scalar and timeline rhythm observations from one Essentia call."""
    try:
        from essentia.standard import RhythmExtractor2013
    except ImportError as exc:
        raise RuntimeError("essentia-tensorflow is required for rhythm extraction") from exc
    max_samples = RHYTHM_MAX_DURATION_SECONDS * ESSENTIA_RHYTHM_SAMPLE_RATE
    rhythm_audio = audio[:max_samples] if audio.size > max_samples else audio
    bpm, beats, beats_confidence, estimates, intervals = RhythmExtractor2013(
        method="multifeature"
    )(rhythm_audio)
    return RhythmAnalysis(
        bpm=float(bpm),
        beats=np.asarray(beats, dtype=np.float32),
        confidence=float(beats_confidence),
        estimates=np.asarray(estimates, dtype=np.float32),
        intervals=np.asarray(intervals, dtype=np.float32),
    )


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
