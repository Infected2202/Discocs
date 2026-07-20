from io import BytesIO
from pathlib import Path

import numpy as np

from app.audio_features import RhythmAnalysis
from app.timeline import extractor
from app.timeline.codec import EXTRACTOR, decode_timeline


class _FakeProcess:
    def __init__(self, samples: np.ndarray):
        self.stdout = BytesIO(samples.astype("<f4").tobytes())
        self.stderr = BytesIO()
        self.killed = False

    def wait(self) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True


def test_one_decode_feeds_waveform_and_rhythm_timeline(monkeypatch, tmp_path: Path):
    samples = np.linspace(-1.0, 1.0, extractor.BUCKET_SAMPLES * 2, dtype=np.float32)
    process = _FakeProcess(samples)
    analyzed = []
    monkeypatch.setattr(extractor.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        extractor,
        "analyze_rhythm",
        lambda audio: analyzed.append(audio.copy()) or RhythmAnalysis(
            bpm=100.0,
            beats=np.array([0.5, 1.0], dtype=np.float32),
            confidence=0.75,
            estimates=np.array([120.0], dtype=np.float32),
            intervals=np.array([0.5], dtype=np.float32),
        ),
    )

    manifest, payload = extractor.extract_timeline(
        tmp_path / "track.flac",
        track_id=7,
        duration=2.0,
        source={"path": "/music/track.flac", "mtime": 1.0, "file_size": 4_096},
    )
    decoded = decode_timeline(manifest, payload)

    assert manifest["extractor"] == EXTRACTOR
    assert np.array_equal(analyzed[0], samples)
    assert decoded["rhythm"]["beats"] == (0.5, 1.0)
    assert decoded["rhythm"]["local_tempo"] == (120.0, 100.0)
    assert decoded["rhythm"]["coverage_seconds"] == samples.size / extractor.SAMPLE_RATE
    assert len(decoded["levels"][0]["arrays"]["minimum"]) == 2


def test_rhythm_audio_retention_stops_at_configured_limit():
    chunks = []

    retained = extractor._append_rhythm_audio(
        chunks, current_samples=0, limit=3, samples=np.arange(5, dtype=np.float32),
    )
    retained = extractor._append_rhythm_audio(
        chunks, current_samples=retained, limit=3, samples=np.arange(2, dtype=np.float32),
    )

    assert retained == 3
    assert np.array_equal(np.concatenate(chunks), np.array([0, 1, 2], dtype=np.float32))
