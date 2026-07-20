"""Timeline encoding from the audio-feature task's shared PCM and rhythm result."""
from __future__ import annotations

import numpy as np

from app.audio_features import RHYTHM_MAX_DURATION_SECONDS, RhythmAnalysis
from app.timeline.codec import EXTRACTOR, encode_timeline

SAMPLE_RATE = 44_100
BUCKET_SAMPLES = 512
_WINDOW = np.hanning(BUCKET_SAMPLES).astype(np.float32)
_FREQUENCIES = np.fft.rfftfreq(BUCKET_SAMPLES, 1 / SAMPLE_RATE)
_BAND_MASKS = (
    _FREQUENCIES < 250,
    (_FREQUENCIES >= 250) & (_FREQUENCIES < 4000),
    _FREQUENCIES >= 4000,
)


def encode_audio_timeline(
    audio: np.ndarray,
    *,
    track_id: int,
    source: dict[str, object],
    rhythm: RhythmAnalysis,
):
    """Encode waveform and beat data from the audio-features task's 44.1 kHz decode."""
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    if not samples.size:
        raise RuntimeError("decoded audio was empty")
    fields: dict[str, list[float]] = {name: [] for name in ("minimum", "maximum", "low", "mid", "high")}
    for start in range(0, samples.size, BUCKET_SAMPLES * 64):
        chunk = samples[start : start + BUCKET_SAMPLES * 64]
        remainder = chunk.size % BUCKET_SAMPLES
        if remainder:
            chunk = np.pad(chunk, (0, BUCKET_SAMPLES - remainder))
        _append_buckets(fields, chunk.reshape(-1, BUCKET_SAMPLES))
    duration = samples.size / SAMPLE_RATE
    coverage = min(samples.size, RHYTHM_MAX_DURATION_SECONDS * SAMPLE_RATE) / SAMPLE_RATE
    return encode_timeline(
        track_id=track_id,
        duration_seconds=duration,
        sample_rate=SAMPLE_RATE,
        base_bucket_samples=BUCKET_SAMPLES,
        base=fields,
        source=source,
        extractor=EXTRACTOR,
        rhythm=_rhythm_series(rhythm, coverage_seconds=coverage),
    )


def _rhythm_series(rhythm: RhythmAnalysis, *, coverage_seconds: float) -> dict[str, object]:
    beats = np.asarray(rhythm.beats, dtype=np.float32)
    intervals = np.asarray(rhythm.intervals, dtype=np.float32)
    local_tempo = np.full(beats.size, rhythm.bpm, dtype=np.float32)
    usable = min(intervals.size, beats.size)
    if usable:
        valid = np.isfinite(intervals[:usable]) & (intervals[:usable] > 0)
        interval_tempo = np.full(usable, rhythm.bpm, dtype=np.float32)
        np.divide(60.0, intervals[:usable], out=interval_tempo, where=valid)
        local_tempo[:usable] = interval_tempo
    local_tempo = np.clip(local_tempo, 20.0, 300.0)
    return {
        "bpm": rhythm.bpm,
        "confidence": rhythm.confidence,
        "coverage_seconds": coverage_seconds,
        "beats": beats.tolist(),
        "local_tempo": local_tempo.tolist(),
    }


def _append_buckets(fields: dict[str, list[float]], samples: np.ndarray) -> None:
    samples = np.nan_to_num(samples.astype(np.float32, copy=False), nan=0.0, posinf=1.0, neginf=-1.0)
    clipped = np.clip(samples, -1.0, 1.0)
    fields["minimum"].extend(clipped.min(axis=1).tolist())
    fields["maximum"].extend(clipped.max(axis=1).tolist())
    spectrum = np.abs(np.fft.rfft(clipped * _WINDOW, axis=1)) ** 2
    totals = spectrum.sum(axis=1)
    for name, mask in zip(("low", "mid", "high"), _BAND_MASKS, strict=True):
        energy = np.divide(spectrum[:, mask].sum(axis=1), totals, out=np.zeros_like(totals), where=totals > 0)
        fields[name].extend(np.clip(energy, 0.0, 1.0).tolist())
