from copy import deepcopy

import pytest

from app.timeline.codec import (
    DESCRIPTOR_ALIGNMENT,
    ENERGY_SCALE,
    EXTRACTOR,
    EXTRACTOR_V1,
    PEAK_SCALE,
    TimelineFormatError,
    decode_timeline,
    encode_timeline,
    manifest_json_bytes,
)


def _artifact():
    base = {
        "minimum": [-0.1, -0.8, -0.2, -1.0, -0.3],
        "maximum": [0.2, 0.7, 0.4, 0.9, 0.1],
        "low": [0.1, 0.2, 0.3, 0.4, 0.5],
        "mid": [0.5, 0.4, 0.3, 0.2, 0.1],
        "high": [0.0, 0.25, 0.5, 0.75, 1.0],
    }
    return encode_timeline(
        track_id=17,
        duration_seconds=1.0,
        sample_rate=2_560,
        base_bucket_samples=512,
        base=base,
        source={"path": "/music/fixture.wav", "mtime": 123.5, "file_size": 4_096},
        extractor=EXTRACTOR_V1,
    )


def test_timeline_encoding_is_deterministic():
    first_manifest, first_payload = _artifact()
    second_manifest, second_payload = _artifact()

    assert first_payload == second_payload
    assert manifest_json_bytes(first_manifest) == manifest_json_bytes(second_manifest)


def test_timeline_descriptors_are_aligned_and_round_trip_dtype_scale():
    manifest, payload = _artifact()
    arrays = manifest["waveform"]["levels"][0]["arrays"]

    assert {descriptor["offset"] % DESCRIPTOR_ALIGNMENT for descriptor in arrays.values()} == {0}
    assert arrays["minimum"]["dtype"] == "int16"
    assert arrays["minimum"]["scale"] == PEAK_SCALE
    assert arrays["low"]["dtype"] == "uint16"
    assert arrays["low"]["scale"] == ENERGY_SCALE
    assert manifest["payload"]["endianness"] == "little"

    decoded = decode_timeline(manifest, payload)["levels"][0]["arrays"]
    assert decoded["minimum"] == pytest.approx((-0.1, -0.8, -0.2, -1.0, -0.3), abs=PEAK_SCALE)
    assert decoded["high"] == pytest.approx((0.0, 0.25, 0.5, 0.75, 1.0), abs=ENERGY_SCALE)


def test_v2_rhythm_events_and_local_tempo_round_trip():
    manifest, payload = encode_timeline(
        track_id=17,
        duration_seconds=2.0,
        sample_rate=2_560,
        base_bucket_samples=512,
        base={
            "minimum": [-0.1], "maximum": [0.1],
            "low": [0.2], "mid": [0.3], "high": [0.5],
        },
        source={"path": "/music/fixture.wav", "mtime": 123.5, "file_size": 4_096},
        extractor=EXTRACTOR,
        rhythm={"bpm": 120.0, "confidence": 0.8, "beats": [0.5, 1.0], "local_tempo": [120.0, 121.0]},
    )

    decoded = decode_timeline(manifest, payload)["rhythm"]

    assert manifest["extractor"] == EXTRACTOR
    assert decoded["bpm"] == 120.0
    assert decoded["confidence"] == 0.8
    assert decoded["beats"] == pytest.approx((0.5, 1.0))
    assert decoded["local_tempo"] == pytest.approx((120.0, 121.0))


def test_waveform_pyramid_preserves_peak_and_energy_extrema():
    bucket_count = 2_049
    base = {
        "minimum": [-0.1] * bucket_count,
        "maximum": [0.1] * bucket_count,
        "low": [0.1] * bucket_count,
        "mid": [0.1] * bucket_count,
        "high": [0.1] * bucket_count,
    }
    base["minimum"][3] = -1.0
    base["maximum"][4] = 1.0
    base["high"][7] = 1.0
    manifest, payload = encode_timeline(
        track_id=1,
        duration_seconds=24,
        sample_rate=44_100,
        base_bucket_samples=512,
        base=base,
        source={"path": "/fixture", "mtime": 1, "file_size": 1},
        extractor=EXTRACTOR_V1,
    )

    decoded_levels = decode_timeline(manifest, payload)["levels"]
    assert len(decoded_levels) == 2
    overview = decoded_levels[1]["arrays"]
    assert min(overview["minimum"]) == pytest.approx(-1.0, abs=PEAK_SCALE)
    assert max(overview["maximum"]) == pytest.approx(1.0, abs=PEAK_SCALE)
    assert max(overview["high"]) == pytest.approx(1.0, abs=ENERGY_SCALE)


@pytest.mark.parametrize(
    ("corrupt", "match"),
    [
        (lambda manifest, payload: (manifest, payload[:-1]), "length mismatch"),
        (
            lambda manifest, payload: (
                {**manifest, "payload": {**manifest["payload"], "sha256": "0" * 64}},
                payload,
            ),
            "checksum mismatch",
        ),
        (lambda manifest, payload: ({**manifest, "schema_version": 2}, payload), "schema version"),
    ],
)
def test_timeline_decoder_rejects_corrupt_artifacts(corrupt, match):
    manifest, payload = _artifact()
    corrupt_manifest, corrupt_payload = corrupt(deepcopy(manifest), payload)

    with pytest.raises(TimelineFormatError, match=match):
        decode_timeline(corrupt_manifest, corrupt_payload)


@pytest.mark.parametrize(
    ("corrupt", "match"),
    [
        (lambda manifest: manifest.update(pack_name="future"), "pack or extractor"),
        (lambda manifest: manifest["waveform"].update(levels=[]), "missing waveform levels"),
        (lambda manifest: manifest["waveform"]["levels"][0]["arrays"]["low"].update(dtype="int16"), "length or dtype"),
    ],
)
def test_timeline_layout_validation_rejects_incompatible_foundation(corrupt, match):
    manifest, payload = _artifact()
    corrupt(manifest)
    with pytest.raises(TimelineFormatError, match=match):
        decode_timeline(manifest, payload)
