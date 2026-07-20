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
EXTRACTOR_V1 = "timeline_foundation_v1"
EXTRACTOR = "timeline_foundation_v2"
ENDIANNESS = "little"
DESCRIPTOR_ALIGNMENT = 4
PYRAMID_FACTOR = 4
MAX_OVERVIEW_BUCKETS = 2_048
MISSING_WAVEFORM_LEVELS = "missing waveform levels"
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


def _encode_rhythm(payload: bytearray, rhythm: Mapping[str, Any], duration_seconds: float) -> dict[str, Any]:
    beats = rhythm.get("beats")
    local_tempo = rhythm.get("local_tempo")
    if not isinstance(beats, Sequence) or not isinstance(local_tempo, Sequence) or len(beats) != len(local_tempo):
        raise TimelineFormatError("rhythm arrays must have one shared length")
    if any(not math.isfinite(float(value)) for value in [*beats, *local_tempo]):
        raise TimelineFormatError("rhythm arrays contain a non-finite value")
    beat_values = [float(value) for value in beats]
    tempo_values = [float(value) for value in local_tempo]
    if any(value < 0 or value > duration_seconds for value in beat_values) or beat_values != sorted(beat_values):
        raise TimelineFormatError("beat timestamps must be ordered within the track")
    if any(value <= 0 for value in tempo_values):
        raise TimelineFormatError("local tempo must be positive")
    try:
        bpm = float(rhythm.get("bpm", 0.0))
        confidence = float(rhythm.get("confidence", 0.0))
        coverage_seconds = float(rhythm.get("coverage_seconds", duration_seconds))
    except (TypeError, ValueError) as exc:
        raise TimelineFormatError("invalid rhythm scalars") from exc
    if (
        not math.isfinite(bpm) or bpm < 0 or not math.isfinite(confidence) or
        not math.isfinite(coverage_seconds) or coverage_seconds <= 0 or coverage_seconds > duration_seconds
    ):
        raise TimelineFormatError("invalid rhythm scalars")
    arrays = {}
    for name, values, unit in (("beats", beat_values, "seconds"), ("local_tempo", tempo_values, "bpm")):
        aligned_offset = _align(len(payload))
        payload.extend(b"\0" * (aligned_offset - len(payload)))
        payload.extend(struct.pack(f"<{len(values)}f", *values))
        arrays[name] = {
            "offset": aligned_offset, "length": len(values), "dtype": "float32", "scale": 1.0, "unit": unit,
        }
    return {"bpm": bpm, "confidence": confidence, "coverage_seconds": coverage_seconds, "arrays": arrays}


def encode_timeline(
    *,
    track_id: int,
    duration_seconds: float,
    sample_rate: int,
    base_bucket_samples: int,
    base: Mapping[str, Sequence[float]],
    source: Mapping[str, Any],
    rhythm: Mapping[str, Any] | None = None,
    extractor: str = EXTRACTOR,
) -> tuple[dict[str, Any], bytes]:
    """Encode normalized waveform buckets and optional v2 rhythm observations."""
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

    rhythm_manifest = None
    if extractor == EXTRACTOR:
        if rhythm is None:
            raise TimelineFormatError("missing rhythm series")
        rhythm_manifest = _encode_rhythm(payload, rhythm, duration_seconds)
    elif extractor != EXTRACTOR_V1:
        raise TimelineFormatError("unsupported timeline extractor")

    payload_bytes = bytes(payload)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "pack_name": PACK_NAME,
        "extractor": extractor,
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
    if rhythm_manifest is not None:
        manifest["rhythm"] = rhythm_manifest
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


def _validate_descriptor_layout(descriptor: Mapping[str, Any], payload_length: int) -> None:
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
    if offset + length * _DTYPE_FORMATS[dtype][1] > payload_length:
        raise TimelineFormatError("descriptor exceeds payload length")


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


def validate_timeline(manifest: Mapping[str, Any], payload: bytes) -> None:
    """Validate v1 metadata and payload without materializing decoded arrays."""
    _validate_payload(manifest, payload)
    extractor = manifest.get("extractor")
    if manifest.get("pack_name") != PACK_NAME or extractor not in {EXTRACTOR_V1, EXTRACTOR}:
        raise TimelineFormatError("unsupported timeline pack or extractor")
    _validate_waveform_manifest(manifest.get("waveform"), len(payload))
    if extractor == EXTRACTOR:
        _validate_rhythm_manifest(manifest.get("rhythm"), len(payload), manifest.get("duration_seconds"))


def _validate_rhythm_manifest(rhythm: object, payload_length: int, duration_seconds: object) -> None:
    if not isinstance(rhythm, Mapping) or not isinstance(rhythm.get("arrays"), Mapping):
        raise TimelineFormatError("missing rhythm series")
    bpm = rhythm.get("bpm")
    confidence = rhythm.get("confidence")
    coverage_seconds = rhythm.get("coverage_seconds")
    if not isinstance(bpm, (int, float)) or not math.isfinite(float(bpm)) or float(bpm) < 0:
        raise TimelineFormatError("invalid rhythm bpm")
    if not isinstance(confidence, (int, float)) or not math.isfinite(float(confidence)):
        raise TimelineFormatError("invalid rhythm confidence")
    if (
        not isinstance(coverage_seconds, (int, float)) or not math.isfinite(float(coverage_seconds)) or
        float(coverage_seconds) <= 0 or not isinstance(duration_seconds, (int, float)) or
        float(coverage_seconds) > float(duration_seconds)
    ):
        raise TimelineFormatError("invalid rhythm coverage")
    arrays = rhythm["arrays"]
    if set(arrays) != {"beats", "local_tempo"}:
        raise TimelineFormatError("rhythm series has unexpected arrays")
    lengths = set()
    for descriptor in arrays.values():
        if not isinstance(descriptor, Mapping):
            raise TimelineFormatError("invalid rhythm descriptor")
        _validate_descriptor_layout(descriptor, payload_length)
        if descriptor.get("dtype") != "float32":
            raise TimelineFormatError("rhythm arrays must use float32")
        lengths.add(descriptor.get("length"))
    if len(lengths) != 1:
        raise TimelineFormatError("rhythm arrays must have one shared length")


def _validate_waveform_manifest(waveform: object, payload_length: int) -> None:
    if not isinstance(waveform, Mapping) or not isinstance(waveform.get("levels"), list):
        raise TimelineFormatError(MISSING_WAVEFORM_LEVELS)
    if not waveform["levels"]:
        raise TimelineFormatError(MISSING_WAVEFORM_LEVELS)
    for level in waveform["levels"]:
        _validate_level_layout(level, payload_length)


def _validate_level_layout(level: object, payload_length: int) -> None:
    if not isinstance(level, Mapping) or not isinstance(level.get("arrays"), Mapping):
        raise TimelineFormatError("invalid waveform level")
    arrays = level["arrays"]
    if set(arrays) != set(_FIELD_FORMATS):
        raise TimelineFormatError("waveform level has unexpected arrays")
    bucket_count = level.get("bucket_count")
    if not isinstance(bucket_count, int) or bucket_count <= 0:
        raise TimelineFormatError("invalid waveform bucket count")
    for field, descriptor in arrays.items():
        if not isinstance(descriptor, Mapping):
            raise TimelineFormatError("invalid array descriptor")
        _validate_descriptor_layout(descriptor, payload_length)
        if descriptor.get("length") != bucket_count or descriptor.get("dtype") != _FIELD_FORMATS[field][0]:
            raise TimelineFormatError("waveform array length or dtype mismatch")


def _decode_waveform_levels(waveform: Mapping[str, Any], payload: bytes) -> list[dict[str, Any]]:
    if not isinstance(waveform.get("levels"), list):
        raise TimelineFormatError(MISSING_WAVEFORM_LEVELS)
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
    validate_timeline(manifest, payload)
    waveform = manifest.get("waveform")
    if not isinstance(waveform, Mapping):
        raise TimelineFormatError(MISSING_WAVEFORM_LEVELS)
    decoded_levels = _decode_waveform_levels(waveform, payload)
    result = {"duration_seconds": manifest.get("duration_seconds"), "levels": decoded_levels}
    rhythm = manifest.get("rhythm")
    if isinstance(rhythm, Mapping) and isinstance(rhythm.get("arrays"), Mapping):
        result["rhythm"] = {
            "bpm": rhythm.get("bpm"),
            "confidence": rhythm.get("confidence"),
            "coverage_seconds": rhythm.get("coverage_seconds"),
            "beats": _decode_descriptor(rhythm["arrays"]["beats"], payload),
            "local_tempo": _decode_descriptor(rhythm["arrays"]["local_tempo"], payload),
        }
    return result
