# Slice 0.3 Signalsmith results

**Date:** 2026-07-20

**Package:** `signalsmith-stretch` 1.3.2, exact dependency and lock-file version

**License:** MIT, copyright 2022 Geraint Luff / Signalsmith Audio Ltd.

## Packaging and browser validation

The official package contains an ESM/UMD module with the WASM payload embedded as a data
URI. It generates a Blob worklet by default. Discocs instead imports the official ESM file
with Vite's `?url` handling and assigns that emitted URL to `moduleUrl` before node creation.
This gives the production build an explicit same-origin, content-hashed worklet asset and
avoids a Blob URL dependency. The main-thread factory remains a dynamic import, while no
separate `.wasm` network URL is required by version 1.3.2.

The temporary Vite browser harness successfully loaded that module into a real
`AudioWorklet`, initialized embedded WASM, connected it to a 48 kHz `AudioContext`, appended
stereo chunks by transfer, and exercised start, scheduled rate/loop/seek changes, stop and
buffer drop. The worklet reported an empty `{start: 0, end: 0}` extent after dropping all
buffers. The harness was removed after measurement.

## Measurements

| 48 kHz configuration | Reported latency | Configure RPC |
|---|---:|---:|
| `default` | 120 ms | 1.1 ms |
| `cheaper` | 140 ms | 0.6 ms |

Initial module/worklet/WASM readiness took 67.4 ms in the measured Chromium session. The
cheaper preset has lower setup cost but 20 ms more latency because its split-computation
configuration adds an interval. For clean scheduled changes Discocs uses the reported
latency plus a 20 ms message/scheduling margin: 140 ms for default and 160 ms for cheaper.
Default remains the quality baseline; cheaper is a future explicit degraded/performance
choice, not an automatic switch in this spike.

These timings are environment observations, not universal constants. Runtime code always
queries `latency()` after configuration and derives the lead time from the reported value.

## Buffer and lifecycle decision

Decoded audio will be delivered as bounded, transferable channel chunks. `append()`
transfers each chunk's `ArrayBuffer` to the worklet, so the main thread does not retain a
second PCM copy. `dropBefore()` releases consumed chunks while later chunks continue to be
appended. Phase 6 can therefore use a rolling decode/look-ahead window instead of retaining
whole-track PCM.

`StretchAdapter` owns scheduling, latency compensation and worklet buffers. Release stops
the node, drops every buffer, disconnects output and closes its message port exactly once.
Capability failures distinguish missing AudioWorklet, unavailable WASM, CSP/worklet blocking
and generic initialization errors.

## Exit decision

`StretchDeckSource` remains feasible behind the existing `DeckSource` interface. No command
or mixer-graph correction is required: the future source will compose the validated adapter
with a decoder/chunk feeder and expose the adapter's `AudioNode` as `output`. Failure of this
path remains isolated to Phase 6 tempo/sync; Phase 1 ordinary HTML media playback is unchanged.
