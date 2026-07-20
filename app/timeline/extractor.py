"""Streaming waveform-foundation extraction through the runtime ffmpeg decoder."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from app.timeline.codec import encode_timeline

SAMPLE_RATE = 44_100
BUCKET_SAMPLES = 512
_WINDOW = np.hanning(BUCKET_SAMPLES).astype(np.float32)
_FREQUENCIES = np.fft.rfftfreq(BUCKET_SAMPLES, 1 / SAMPLE_RATE)
_BAND_MASKS = (
    _FREQUENCIES < 250,
    (_FREQUENCIES >= 250) & (_FREQUENCIES < 4000),
    _FREQUENCIES >= 4000,
)


def extract_waveform(path: Path, *, track_id: int, duration: float, source: dict[str, object]):
    process = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(SAMPLE_RATE), "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if process.stdout is None:
        raise RuntimeError("ffmpeg audio pipe is unavailable")
    fields: dict[str, list[float]] = {name: [] for name in ("minimum", "maximum", "low", "mid", "high")}
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
            pending = pending[usable:]
        if pending:
            samples = np.frombuffer(pending[:len(pending) - len(pending) % 4], dtype="<f4")
            if samples.size:
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
    decoded_duration = len(fields["minimum"]) * BUCKET_SAMPLES / SAMPLE_RATE
    return encode_timeline(
        track_id=track_id, duration_seconds=duration if duration > 0 else decoded_duration, sample_rate=SAMPLE_RATE,
        base_bucket_samples=BUCKET_SAMPLES, base=fields, source=source,
    )


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
