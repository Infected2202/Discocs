# Track Timeline Analysis Pack technical plan

**Status:** accepted v1 contract (Slice 0.4)
**Applies to:** Phases 0, 4 and 5

## 1. Objective

Provide one versioned, reusable, browser-efficient description of audio over the track timeline. It supports waveform rendering, beat-aware transport and future transition algorithms without embedding decisions such as best cue, loop or transition point.

The existing `track_features` table is not used for timeline arrays. It is intentionally shaped for one scalar/text value per feature and extractor.

## 2. Artifact split

Timeline v1 has one manifest and one binary payload per track/extractor version:

```text
data/timeline/<track-id>/<extractor-id>/manifest.json
data/timeline/<track-id>/<extractor-id>/payload.bin
```

The manifest is small JSON used for availability, validation and buffer slicing. The payload concatenates little-endian typed arrays and is fetched once for a prepared deck in the foundation. The descriptor keeps byte offsets so later range/lazy loading does not require a format change.

The data directory is runtime state and remains ignored by git.

## 3. Identity and invalidation

Artifact identity includes:

- track id;
- pack name (`timeline_foundation`);
- extractor id, initially `timeline_foundation_v1`;
- schema version, initially `1`;
- source path;
- source mtime;
- source file size.

An artifact is usable only when all source identity fields match the current track row and both files pass manifest size/checksum validation. Any extractor/schema change produces a new identity; a file change invalidates by path + mtime + file size, matching project rules.

Writes use a temporary sibling path followed by an atomic replace. The database record is committed only after both final files are present and checksummed.

## 4. Database ownership

Add a timeline-specific Store mixin, not methods in `app/store/__init__.py`.

Conceptual table:

```sql
CREATE TABLE track_timeline_artifacts (
    track_id INTEGER NOT NULL,
    pack_name TEXT NOT NULL,
    extractor TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    source_path TEXT NOT NULL,
    source_mtime REAL NOT NULL,
    source_file_size INTEGER NOT NULL,
    manifest_path TEXT NOT NULL,
    payload_path TEXT NOT NULL,
    payload_bytes INTEGER NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (track_id, pack_name, extractor),
    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
);
```

The Store mixin owns round-trip, status, stale detection, upsert and delete operations. File deletion occurs only after resolving and validating that the target is inside the configured timeline artifact root.

## 5. Manifest v1

Illustrative schema:

```json
{
  "schema_version": 1,
  "pack_name": "timeline_foundation",
  "extractor": "timeline_foundation_v1",
  "track_id": 123,
  "duration_seconds": 412.34,
  "source": {
    "path": "/music/example.flac",
    "mtime": 1720000000.0,
    "file_size": 81234567
  },
  "waveform": {
    "sample_rate": 44100,
    "base_bucket_samples": 512,
    "levels": []
  },
  "series": {},
  "payload": {
    "byte_length": 0,
    "sha256": "...",
    "endianness": "little"
  }
}
```

Every array descriptor contains:

```json
{
  "offset": 0,
  "length": 1000,
  "dtype": "int16",
  "scale": 0.000030518509476,
  "unit": "linear_amplitude"
}
```

Allowed v1 dtypes are `int16`, `uint16`, `uint8` and `float32`. Consumers reject unknown schema versions/dtypes instead of guessing.

All descriptor offsets are aligned to 4 bytes. Payload integers and floats are
little-endian. Signed peaks use the symmetric range `[-32767, 32767]` with
scale `1 / 32767`; normalized energy uses `[0, 65535]` with scale `1 / 65535`.
The manifest declares `descriptor_alignment: 4` and consumers reject a layout
or endianness they do not support. Padding bytes are zero-filled and covered by
the payload checksum.

## 6. Waveform pyramid

Base resolution starts at 512 source samples per bucket at 44.1 kHz (approximately 11.6 ms). Each following level groups four previous buckets until the coarsest level can draw the full track with at most roughly 2,048 buckets.

Each level contains:

- signed minimum peak (`int16`);
- signed maximum peak (`int16`);
- low-band energy (`uint16`);
- mid-band energy (`uint16`);
- high-band energy (`uint16`).

The first version uses a mono display envelope derived from all source channels. Frequency-colour energy bands use documented fixed boundaries, initially low below 250 Hz, mid 250 Hz-4 kHz and high above 4 kHz. Exact filter implementation is validated in the extractor spike; changing boundaries requires a new extractor id.

Pyramid aggregation takes the minimum of signed minima and the maximum of
signed maxima and energy values in every four-bucket group. The final partial
group is retained. Max aggregation intentionally preserves short energy
transients at overview resolutions; alternative RMS/mean summaries require a
new extractor identity.

Pixi selects the coarsest level that still supplies at least one bucket per horizontal pixel. It never resamples the complete base array on every frame.

## 7. Timeline series

Required v1 series:

- beat timestamps (`float32` seconds) and confidence (`uint8` normalized);
- downbeat timestamps and bar indices where available;
- meter and its confidence as manifest scalars;
- local tempo sampled on a documented regular grid;
- onset/transient strength;
- short-term loudness/energy;
- low/mid/high energy curves;
- structural novelty/boundary strength.

Regular curves share a 100 ms grid unless an extractor requires a separately declared step. Each descriptor declares `start_seconds`, `step_seconds`, unit, scale and missing-value policy. Event series use timestamp arrays rather than a regular grid.

The pack stores observations only. It must not include recommended cue points, loops, transition-safe segments, manoeuvre names or suitability scores.

## 8. Extraction pipeline

- Reuse the existing durable analysis job/task mechanism and worker audio acquisition path.
- Use lazy imports for Essentia/heavy dependencies.
- Decode once per task where practical and derive waveform/energy products from the same audio pass.
- Bound memory: waveform aggregation and regular curves should stream/chunk; the extractor must not require multiple whole-track PCM copies.
- Separate extraction computation from encoding so unit tests can use small NumPy fixtures and fake extractors.
- Publish progress and failures using existing job diagnostics.
- A reset/rebuild deletes only the selected pack/extractor after a replacement task has been accepted.

Optional vocal/drum/bass/melodic/chroma/stem packs use different `pack_name` and extractor identities. They do not mutate the foundation payload.

## 9. API contract

Authenticated endpoints, exact naming subject only to normal API consistency:

```text
GET  /api/v1/tracks/{track_id}/timeline/manifest
GET  /api/v1/tracks/{track_id}/timeline/payload
POST /api/v1/timeline/status
POST /api/v1/jobs/analyze-timeline
```

Requirements:

- manifest returns `404` when absent and `409` with structured stale details when source identity no longer matches;
- payload validates the same identity and sets private cache headers plus ETag/checksum;
- status accepts bounded track-id batches for queue/deck preparation and reports `missing`, `queued`, `running`, `ready`, `stale` or `failed`;
- analyze job supports optional track ids, limit, reset and extractor fields consistent with existing job APIs;
- no endpoint returns arbitrary server filesystem paths to ordinary clients;
- payload access remains protected by the existing authenticated media boundary.

The prepared-deck client fetches the manifest first, rejects unsupported versions, then fetches and verifies the payload length before declaring analysis ready.

## 10. Pixi renderer contract

One renderer instance may host both detailed deck surfaces, or each deck may own a private instance after the Phase 0 measurement. The public component boundary is independent of that choice.

Renderer inputs:

- decoded immutable timeline pack;
- viewport width/height and device-pixel ratio;
- engine playhead anchor;
- zoom/follow state;
- cue, loop, beat/bar and automation overlays;
- Pointer Events translated to timeline seconds.

Renderer rules:

- PixiJS v8 initialization is asynchronous and cleanup destroys the application, ticker listeners and GPU resources;
- use WebGL as the first-release preference and capability fallback;
- use a private ticker capped at 60 FPS and stop it when the surface is hidden;
- resize from its containing element, not global window assumptions;
- draw geometry is cached per artifact level/zoom band rather than rebuilt every frame;
- engine time is authoritative; Pixi interpolation is presentation only;
- overview and detailed surfaces share decoded arrays and do not duplicate payload memory.

## 11. Settings ownership

Instance-wide private admin:

- extractor/schema readiness;
- missing/stale/failed counts;
- queue/rebuild controls;
- artifact root usage and cleanup;
- optional pack enablement;
- extractor diagnostics.

Per-user authenticated settings:

- waveform colour preset;
- default zoom and follow behaviour;
- overlay visibility;
- pointer/touch interaction preferences.

## 12. Tests

Tests are authored with the implementation and run in Jenkins.

### Store and lifecycle

- artifact metadata upsert/round-trip;
- exact path + mtime + file-size invalidation;
- extractor/schema coexistence and replacement;
- checksum/length mismatch rejection;
- safe cleanup constrained to artifact root;
- track deletion cascade and orphan cleanup.

### Encoding

- deterministic manifest and payload from a small fixture;
- descriptor offsets, alignment, dtype, scale and endianness;
- pyramid aggregation preserves extrema;
- quantization bounds and decode round-trip;
- corrupt/unknown manifest rejection in Python and TypeScript decoders.

### API/jobs

- missing, stale, ready and failure paths;
- authenticated manifest/payload access;
- batch status bounds;
- rebuild/reset task creation and resume behaviour;
- worker failure does not publish a partial artifact.

### Renderer/client

- level selection at representative zooms;
- time/pixel conversion and pointer seek;
- cleanup on hide/unmount;
- unsupported schema and truncated payload states;
- engine clock remains the playhead source of truth.

## 13. Phase 0 format spike

Generate fixtures for short, typical and long tracks and record:

- manifest/payload bytes per minute;
- extractor wall time and peak memory;
- browser parse time and retained memory;
- Pixi frame time at overview and maximum useful zoom;
- visual usefulness of the proposed frequency bands;
- beat/downbeat confidence behaviour on representative library material.

Slice 0.4 accepted the 512-sample base bucket, factor-4 pyramid, 4-byte
descriptor alignment, little-endian layout and quantization rules above.
`app.timeline.codec` is the offline reference encoder/decoder,
`app.timeline.fixtures` generates short/typical/long synthetic artifacts, and
`ui/src/engine/timeline/decoder.ts` is the browser contract implementation.
Measurements and size/memory estimates are recorded in
`SLICE_0_4_FORMAT_RESULTS.md`.

Once Phase 4 implementation begins, any incompatible change increments the
extractor id or schema version.
