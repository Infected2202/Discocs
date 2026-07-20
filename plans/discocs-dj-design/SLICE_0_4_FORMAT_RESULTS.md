# Slice 0.4 timeline v1 format results

**Result:** accepted

## Fixture contract

The offline reference encoder generated deterministic 44.1 kHz fixtures with
a 512-sample base bucket and factor-4 extrema pyramid. Files are reproducible
with `scripts/generate_timeline_fixtures.py`; generated manifests and payloads
remain untracked runtime/evaluation output.

| Fixture | Duration | Bucket counts by level | Payload | Bytes/minute |
|---|---:|---|---:|---:|
| short | 30 s | 2,584 / 646 | 32,300 B | 64,600 B |
| typical | 6 min | 31,008 / 7,752 / 1,938 | 406,980 B | 67,830 B |
| long | 60 min | 310,079 / 77,520 / 19,380 / 4,845 / 1,212 | 4,130,380 B | 68,839.67 B |

The asymptotic waveform payload cost is about 67.2 KiB/minute. Small
differences come from partial pyramid groups and alignment padding.

## Decode and render memory estimate

The browser decoder keeps one fetched `ArrayBuffer` and exposes aligned typed
array views into it, so decoding does not copy sample storage. A prepared
60-minute track therefore retains about 3.94 MiB of waveform payload plus a
small manifest/view-object overhead; two prepared decks retain about 7.88 MiB.

The renderer selects a level near one bucket per horizontal pixel and does not
materialize the complete base level as geometry. At the frozen 2,048-bucket
overview bound, a conservative four-float endpoint plus colour estimate is
about 40 KiB per surface (about 80 KiB for two), excluding Pixi/WebGL allocator
overhead already measured in Slice 0.2. Maximum useful zoom remains bounded by
the viewport bucket span rather than whole-track duration.

## Validation outcome

- Python reference encoding is canonical JSON plus deterministic binary data.
- Descriptor offset, length, dtype, scale and 4-byte alignment round-trip.
- Pyramid minima, maxima and band-energy transients preserve extrema.
- Python and TypeScript decoders reject unsupported versions, corrupt lengths,
  unknown dtypes/layouts and SHA-256 mismatches.
- Typed arrays rely on the declared little-endian payload contract. Supported
  browsers are little-endian; a future non-little-endian client must copy and
  byte-swap rather than reinterpret the payload.

## Frozen v1 decisions

- schema version `1`, extractor `timeline_foundation_v1`;
- little-endian numeric representation;
- 4-byte descriptor alignment with checksummed zero padding;
- signed peak scale `1/32767`, normalized energy scale `1/65535`;
- 512 source samples per base bucket at source sample rate;
- factor-4 pyramid with final partial groups retained;
- min aggregation for minima and max aggregation for maxima/energy.
