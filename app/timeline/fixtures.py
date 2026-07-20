"""Synthetic timeline fixture generation for format sizing and interoperability."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from app.timeline.codec import encode_timeline, manifest_json_bytes

FIXTURE_DURATIONS = {"short": 30.0, "typical": 360.0, "long": 3_600.0}
SAMPLE_RATE = 44_100
BASE_BUCKET_SAMPLES = 512


def synthetic_base(duration_seconds: float) -> dict[str, list[float]]:
    bucket_count = math.ceil(duration_seconds * SAMPLE_RATE / BASE_BUCKET_SAMPLES)
    phase_step = 2 * math.pi / max(bucket_count, 1)
    envelope = [0.08 + 0.9 * abs(math.sin(index * phase_step * 7)) for index in range(bucket_count)]
    return {
        "minimum": [-value for value in envelope],
        "maximum": envelope,
        "low": [value * (0.45 + 0.2 * math.sin(index * phase_step)) for index, value in enumerate(envelope)],
        "mid": [value * (0.55 + 0.15 * math.cos(index * phase_step * 3)) for index, value in enumerate(envelope)],
        "high": [value * (0.3 + 0.2 * abs(math.sin(index * phase_step * 11))) for index, value in enumerate(envelope)],
    }


def build_fixture(name: str, duration_seconds: float) -> tuple[dict[str, Any], bytes]:
    return encode_timeline(
        track_id={"short": 1, "typical": 2, "long": 3}.get(name, 99),
        duration_seconds=duration_seconds,
        sample_rate=SAMPLE_RATE,
        base_bucket_samples=BASE_BUCKET_SAMPLES,
        base=synthetic_base(duration_seconds),
        source={"path": f"/fixtures/{name}.wav", "mtime": 1_720_000_000.0, "file_size": 0},
    )


def generate_fixture_set(output_directory: Path) -> list[dict[str, float | int | str]]:
    output_directory.mkdir(parents=True, exist_ok=True)
    results = []
    for name, duration_seconds in FIXTURE_DURATIONS.items():
        manifest, payload = build_fixture(name, duration_seconds)
        (output_directory / f"{name}.manifest.json").write_bytes(manifest_json_bytes(manifest))
        (output_directory / f"{name}.payload.bin").write_bytes(payload)
        results.append(
            {
                "name": name,
                "duration_seconds": duration_seconds,
                "payload_bytes": len(payload),
                "bytes_per_minute": round(len(payload) / (duration_seconds / 60), 2),
                "decoded_bytes": sum(
                    descriptor["length"] * (2 if descriptor["dtype"] != "uint8" else 1)
                    for level in manifest["waveform"]["levels"]
                    for descriptor in level["arrays"].values()
                ),
            }
        )
    return results
