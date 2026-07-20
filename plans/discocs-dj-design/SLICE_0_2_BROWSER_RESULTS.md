# Slice 0.2 browser results

**Date:** 2026-07-20

**PixiJS:** 8.16.0 (exact dependency and lock-file version)

**Surface:** two synthetic 240-second, multi-resolution, frequency-coloured waveforms

## Result

PixiJS v8 initialized asynchronously in React-owned containers with WebGL preference,
private tickers capped at 60 FPS and container-based resize. Both deck surfaces rendered
at the same time. Zoom and follow changes selected new viewport geometry without creating
additional canvases. Pointer positions are converted against the current viewport and
clamped to its time range.

The retained production modules are `ui/src/engine/waveform/`. The temporary Vite browser
harness and synthetic data generator used for measurement were removed after the spike,
so no prototype route or fixture is shipped in the runtime bundle.

## Measurements

The in-app Chromium renderer was exercised at 1280 x 180 CSS pixels per deck (two decks,
DPR 1) and at 800 x 180 after a live container resize.

| Scenario | Result |
|---|---:|
| Two 1280 x 180 surfaces, steady 120-frame sample | mean 6.90 ms, p95 11.10 ms |
| Mounted canvases after zoom/follow changes | 2 |
| Canvases after unmounting both surfaces | 0 |
| Canvases after remounting both surfaces | 2 |
| Live resize result | two 800 x 180 backing canvases |
| Chromium JS heap before teardown | 175.8 MiB |
| Chromium JS heap after teardown settled | 95.8 MiB |

The heap figures include Vite, React, PixiJS and the synthetic arrays and are therefore a
lifecycle check rather than a package allocation benchmark. The approximately 80 MiB drop,
zero retained canvases and deterministic destroy calls show no observable retained renderer
allocation after teardown. Browser-exposed JS heap does not include a reliable per-context
GPU allocation, so GPU cleanup is additionally enforced by `Application.destroy(true, ...)`
and covered by a lifecycle test.

## Decision

Use **one private Pixi application per detailed deck surface** for the first implementation.
Two applications remained inside the desktop frame budget and teardown released their
observable resources. Per-deck ownership also lets hidden or unmounted deck surfaces stop
and release independently, without coupling React layout to a single canvas spanning two
containers.

The public `WaveformRendererInput` is frozen around an immutable timeline, viewport/DPR,
authoritative playhead, zoom/follow state and palette. It does not depend on backend payload
transport. A later renderer can share decoded timeline arrays between applications without
changing this boundary.
