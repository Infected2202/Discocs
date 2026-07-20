"""Deterministic timeline v1 fixture codec.

This module deliberately has no Store, HTTP, or audio-decoder dependency. Phase 4
can use the same contract after extraction has produced normalized base buckets.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from collections.abc import Mapping, Sequence
from typing import Any

SCHEMA_VERSION = 1
PACK_NAME = "timeline_foundation"
EXTRACTOR = "timeline_foundation_v1"
ENDIANNESS = "little"
DESCRIPTOR_ALIGNMENT = 4
PYRAMID_FACTOR = 4
MAX_OVERVIEW_BUCKETS = 2_048
PEAK_SCALE = 1.0 / 32_767.0
ENERGY_SCALE = 1.0 / 65_535.0

_FIELD_FORMATS = {
    "minimum": ("int16", PEAK_SCALE, "linear_amplitude"),
    "maximum": ("int16", PEAK_SCALE, "linear_amplitude"),
    "low": ("uint16", ENERGY_SCALE, "normalized_energy"),
    "mid": ("uint16", ENERGY_SCALE, "normalized_energy"),
    "high": ("uint16", ENERGY_SCALE, "normalized_energy"),
}
_DTYPE_FORMATS = {
    "int16": ("h", 2),
    "uint16": ("H", 2),
    "uint8": ("B", 1),
    "float32": ("f", 4),
}


class TimelineFormatError(ValueError):
    """Raised when a timeline manifest or payload violates the v1 contract."""


def _align(value: int) -> int:
    return (value + DESCRIPTOR_ALIGNMENT - 1) // DESCRIPTOR_ALIGNMENT * DESCRIPTOR_ALIGNMENT


def _quantize(values: Sequence[float], dtype: str) -> list[int]:
    maximum = 32_767 if dtype == "int16" else 65_535
    minimum = -32_767 if dtype == "int16" else 0
    return [max(minimum, min(maximum, round(float(value) * maximum))) for value in values]


def _aggregate(values: Sequence[float], reducer: str) -> list[float]:
    result: list[float] = []
    for offset in range(0, len(values), PYRAMID_FACTOR):
        group = values[offset : offset + PYRAMID_FACTOR]
        result.append(min(group) if reducer == "min" else max(group))
    return result


def _validate_base(base: Mapping[str, Sequence[float]]) -> int:
    missing = set(_FIELD_FORMATS) - set(base)
    if missing:
        raise TimelineFormatError(f"missing waveform fields: {sorted(missing)}")
    lengths = {len(base[field]) for field in _FIELD_FORMATS}
    if len(lengths) != 1 or not lengths or next(iter(lengths)) == 0:
        raise TimelineFormatError("waveform fields must have one shared non-zero length")
    for field, values in base.items():
        if field not in _FIELD_FORMATS:
            continue
        if any(not math.isfinite(float(value)) for value in values):
            raise TimelineFormatError(f"{field} contains a non-finite value")
    return next(iter(lengths))


def _build_pyramid(base: Mapping[str, Sequence[float]]) -> list[dict[str, list[float]]]:
    _validate_base(base)
    levels = [{field: [float(value) for value in base[field]] for field in _FIELD_FORMATS}]
    while len(levels[-1]["minimum"]) > MAX_OVERVIEW_BUCKETS:
        previous = levels[-1]
        levels.append(
            {
                field: _aggregate(values, "min" if field == "minimum" else "max")
                for field, values in previous.items()
            }
        )
    return levels


def encode_timeline(
    *,
    track_id: int,
    duration_seconds: float,
    sample_rate: int,
    base_bucket_samples: int,
    base: Mapping[str, Sequence[float]],
    source: Mapping[str, Any],
) -> tuple[dict[str, Any], bytes]:
    """Encode normalized waveform buckets into a deterministic v1 artifact."""
    if track_id <= 0 or duration_seconds <= 0 or sample_rate <= 0 or base_bucket_samples <= 0:
        raise TimelineFormatError("track, duration, sample rate and bucket size must be positive")

    payload = bytearray()
    manifest_levels: list[dict[str, Any]] = []
    for level_index, level in enumerate(_build_pyramid(base)):
        descriptors: dict[str, Any] = {}
        for field, (dtype, scale, unit) in _FIELD_FORMATS.items():
            aligned_offset = _align(len(payload))
            payload.extend(b"\0" * (aligned_offset - len(payload)))
            values = _quantize(level[field], dtype)
            format_code, _ = _DTYPE_FORMATS[dtype]
            payload.extend(struct.pack(f"<{len(values)}{format_code}", *values))
            descriptors[field] = {
                "offset": aligned_offset,
                "length": len(values),
                "dtype": dtype,
                "scale": scale,
                "unit": unit,
            }
        manifest_levels.append(
            {
                "level": level_index,
                "bucket_samples": base_bucket_samples * (PYRAMID_FACTOR**level_index),
                "bucket_count": len(level["minimum"]),
                "arrays": descriptors,
            }
        )

    payload_bytes = bytes(payload)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "pack_name": PACK_NAME,
        "extractor": EXTRACTOR,
        "track_id": track_id,
        "duration_seconds": float(duration_seconds),
        "source": {
            "path": str(source["path"]),
            "mtime": float(source["mtime"]),
            "file_size": int(source["file_size"]),
        },
        "waveform": {
            "sample_rate": sample_rate,
            "base_bucket_samples": base_bucket_samples,
            "pyramid_factor": PYRAMID_FACTOR,
            "levels": manifest_levels,
        },
        "series": {},
        "payload": {
            "byte_length": len(payload_bytes),
            "sha256": hashlib.sha256(payload_bytes).hexdigest(),
            "endianness": ENDIANNESS,
            "descriptor_alignment": DESCRIPTOR_ALIGNMENT,
        },
    }
    return manifest, payload_bytes


def manifest_json_bytes(manifest: Mapping[str, Any]) -> bytes:
    """Return canonical JSON bytes for reproducible fixture generation."""
    return (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _decode_descriptor(descriptor: Mapping[str, Any], payload: bytes) -> tuple[float, ...]:
    dtype = descriptor.get("dtype")
    if dtype not in _DTYPE_FORMATS:
        raise TimelineFormatError(f"unsupported dtype: {dtype}")
    offset = descriptor.get("offset")
    length = descriptor.get("length")
    scale = descriptor.get("scale")
    if not isinstance(offset, int) or offset < 0 or offset % DESCRIPTOR_ALIGNMENT:
        raise TimelineFormatError("descriptor offset is not aligned")
    if not isinstance(length, int) or length < 0 or not isinstance(scale, (int, float)):
        raise TimelineFormatError("invalid descriptor length or scale")
    format_code, byte_width = _DTYPE_FORMATS[dtype]
    byte_length = length * byte_width
    if offset + byte_length > len(payload):
        raise TimelineFormatError("descriptor exceeds payload length")
    values = struct.unpack_from(f"<{length}{format_code}", payload, offset)
    return tuple(float(value) * float(scale) for value in values)


def _validate_payload(manifest: Mapping[str, Any], payload: bytes) -> None:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise TimelineFormatError("unsupported timeline schema version")
    payload_meta = manifest.get("payload")
    if not isinstance(payload_meta, Mapping):
        raise TimelineFormatError("missing payload descriptor")
    if payload_meta.get("endianness") != ENDIANNESS:
        raise TimelineFormatError("unsupported payload endianness")
    if payload_meta.get("descriptor_alignment") != DESCRIPTOR_ALIGNMENT:
        raise TimelineFormatError("unsupported descriptor alignment")
    if payload_meta.get("byte_length") != len(payload):
        raise TimelineFormatError("payload length mismatch")
    if payload_meta.get("sha256") != hashlib.sha256(payload).hexdigest():
        raise TimelineFormatError("payload checksum mismatch")


def _decode_waveform_levels(waveform: Mapping[str, Any], payload: bytes) -> list[dict[str, Any]]:
    if not isinstance(waveform.get("levels"), list):
        raise TimelineFormatError("missing waveform levels")
    decoded_levels = []
    for level in waveform["levels"]:
        if not isinstance(level, Mapping) or not isinstance(level.get("arrays"), Mapping):
            raise TimelineFormatError("invalid waveform level")
        arrays = level["arrays"]
        if set(arrays) != set(_FIELD_FORMATS):
            raise TimelineFormatError("waveform level has unexpected arrays")
        decoded = {field: _decode_descriptor(arrays[field], payload) for field in _FIELD_FORMATS}
        if {len(values) for values in decoded.values()} != {level.get("bucket_count")}:
            raise TimelineFormatError("waveform array length mismatch")
        decoded_levels.append({**level, "arrays": decoded})
    return decoded_levels


def decode_timeline(manifest: Mapping[str, Any], payload: bytes) -> dict[str, Any]:
    """Validate and decode a v1 payload for fixture and backend round trips."""
    _validate_payload(manifest, payload)
    waveform = manifest.get("waveform")
    if not isinstance(waveform, Mapping):
        raise TimelineFormatError("missing waveform levels")
    decoded_levels = _decode_waveform_levels(waveform, payload)
    return {"duration_seconds": manifest.get("duration_seconds"), "levels": decoded_levels}
