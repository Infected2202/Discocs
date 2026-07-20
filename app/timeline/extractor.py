"""Single-decode waveform and rhythm extraction for timeline foundation v2."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from app.audio_features import RHYTHM_MAX_DURATION_SECONDS, RhythmAnalysis, analyze_rhythm
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


def extract_timeline(path: Path, *, track_id: int, duration: float, source: dict[str, object]):
    process = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(SAMPLE_RATE), "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if process.stdout is None:
        raise RuntimeError("ffmpeg audio pipe is unavailable")
    fields: dict[str, list[float]] = {name: [] for name in ("minimum", "maximum", "low", "mid", "high")}
    rhythm_chunks: list[np.ndarray] = []
    rhythm_samples = 0
    rhythm_limit = RHYTHM_MAX_DURATION_SECONDS * SAMPLE_RATE
    pending = b""
    try:
        while True:
            chunk = process.stdout.read(BUCKET_SAMPLES * 4 * 64)
            if not chunk:
                break
            pending += chunk
            usable = len(pending) - (len(pending) % (BUCKET_SAMPLES * 4))
            if usable:
                samples = np.frombuffer(pending[:usable], dtype="<f4").reshape(-1, BUCKET_SAMPLES)
                _append_buckets(fields, samples)
                rhythm_samples = _append_rhythm_audio(rhythm_chunks, rhythm_samples, rhythm_limit, samples.reshape(-1))
            pending = pending[usable:]
        if pending:
            samples = np.frombuffer(pending[:len(pending) - len(pending) % 4], dtype="<f4")
            if samples.size:
                rhythm_samples = _append_rhythm_audio(rhythm_chunks, rhythm_samples, rhythm_limit, samples)
                padded = np.pad(samples, (0, BUCKET_SAMPLES - samples.size))
                _append_buckets(fields, padded.reshape(1, BUCKET_SAMPLES))
        stderr = process.stderr.read().decode(errors="replace") if process.stderr else ""
        if process.wait() != 0:
            raise RuntimeError(stderr.strip() or "ffmpeg failed to decode audio")
    except Exception:
        process.kill()
        process.wait()
        raise
    if not fields["minimum"]:
        raise RuntimeError("decoded audio was empty")
    rhythm_audio = np.concatenate(rhythm_chunks)
    rhythm = analyze_rhythm(rhythm_audio)
    decoded_duration = len(fields["minimum"]) * BUCKET_SAMPLES / SAMPLE_RATE
    return encode_timeline(
        track_id=track_id, duration_seconds=max(duration, decoded_duration), sample_rate=SAMPLE_RATE,
        base_bucket_samples=BUCKET_SAMPLES, base=fields, source=source, extractor=EXTRACTOR,
        rhythm=_rhythm_series(rhythm, coverage_seconds=rhythm_audio.size / SAMPLE_RATE),
    )


def _append_rhythm_audio(
    chunks: list[np.ndarray], current_samples: int, limit: int, samples: np.ndarray,
) -> int:
    remaining = limit - current_samples
    if remaining <= 0:
        return current_samples
    retained = np.asarray(samples[:remaining], dtype=np.float32)
    if retained.size:
        chunks.append(retained.copy())
    return current_samples + retained.size


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
