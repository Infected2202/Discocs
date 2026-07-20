import numpy as np

from app.audio_features import RhythmAnalysis
from app.timeline import extractor
from app.timeline.codec import decode_timeline


def test_encode_audio_timeline_uses_precomputed_rhythm_without_decode():
    audio = np.linspace(-1.0, 1.0, extractor.BUCKET_SAMPLES + 3, dtype=np.float32)
    rhythm = RhythmAnalysis(
        bpm=120.0,
        beats=np.array([0.005], dtype=np.float32),
        confidence=0.8,
        estimates=np.array([], dtype=np.float32),
        intervals=np.array([0.5], dtype=np.float32),
    )
    manifest, payload = extractor.encode_audio_timeline(
        audio,
        track_id=9,
        source={"path": "/music/track.flac", "mtime": 1, "file_size": 2},
        rhythm=rhythm,
    )
    decoded = decode_timeline(manifest, payload)

    assert len(decoded["levels"][0]["arrays"]["minimum"]) == 2
    assert np.isclose(decoded["rhythm"]["beats"][0], 0.005)
