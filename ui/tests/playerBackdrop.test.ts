import assert from "node:assert/strict"
import { test } from "vitest"
import {
  artworkBackdropUrl,
  backdropAnimationState,
  resolvePlasmaLayer,
  resolveBackdropLayers,
} from "../src/components/player/playerBackdropUtils.ts"
import { preloadArtworkImage } from "../src/components/player/playerBarTransitionUtils.ts"
import {
  easeTrackAccentTransition,
  keepPlasmaMountedBetweenTracks,
  mixPlasmaColor,
  parsePlasmaColor,
  plasmaShaderInitializers,
  readTrackAccentTransitionDurationMs,
  readTrackAccentColor,
} from "../src/components/player/plasmaUtils.ts"

test("plasma stays mounted while the next track accent is resolving", () => {
  assert.equal(keepPlasmaMountedBetweenTracks(false, false), false)
  assert.equal(keepPlasmaMountedBetweenTracks(false, true), true)
  assert.equal(keepPlasmaMountedBetweenTracks(true, false), true)
})

test("plasma shader initializes frame accumulators deterministically", () => {
  assert.match(plasmaShaderInitializers, /float i = 0\.0;/)
  assert.match(plasmaShaderInitializers, /float z = 0\.0;/)
  assert.match(plasmaShaderInitializers, /vec3 O = vec3\(0\.0\);/)
})

test("first plasma frame should wait for the current track accent event", () => {
  let plasmaReady = false
  let plasmaAccent = ""
  let resolvedArtworkUrl = ""
  const artworkUrl = "/artwork/current"

  const onArtworkChange = () => {
    plasmaReady = resolvedArtworkUrl === artworkUrl
    plasmaAccent = plasmaReady ? "rgb(10 20 30)" : ""
  }

  const onAccentChange = (nextArtworkUrl: string, nextAccent: string) => {
    resolvedArtworkUrl = nextArtworkUrl
    if (nextArtworkUrl === artworkUrl) {
      plasmaAccent = nextAccent
      plasmaReady = true
    }
  }

  onArtworkChange()
  assert.equal(plasmaReady, false)
  assert.equal(plasmaAccent, "")

  onAccentChange("/artwork/previous", "rgb(1 2 3)")
  assert.equal(plasmaReady, false)
  assert.equal(plasmaAccent, "")

  onAccentChange(artworkUrl, "rgb(12 34 56)")
  assert.equal(plasmaReady, true)
  assert.equal(plasmaAccent, "rgb(12 34 56)")
})

test("artworkBackdropUrl requests an efficient backdrop image size", () => {
  assert.equal(
    artworkBackdropUrl("/artwork/42?size=40"),
    "/artwork/42?size=320"
  )
  assert.equal(
    artworkBackdropUrl("/artwork/42?token=abc#cover"),
    "/artwork/42?token=abc&size=320#cover"
  )
  assert.equal(artworkBackdropUrl(undefined), undefined)
})

test("backdrop motion runs only while audio is playing", () => {
  assert.equal(backdropAnimationState(true), "running")
  assert.equal(backdropAnimationState(false), "paused")
})

test("plasma layer stays mounted across accent readiness, fading via opacity", () => {
  // First track: artwork present but accent not resolved yet — mounted, invisible.
  const loading = resolvePlasmaLayer(true, false)
  assert.equal(loading.mounted, true)
  assert.equal(loading.opacity, 0)

  // Accent resolved — same mount, now visible. Mount must NOT depend on readiness,
  // otherwise the WebGL context would be recreated on every track change.
  const ready = resolvePlasmaLayer(true, true)
  assert.equal(ready.mounted, true)
  assert.equal(ready.opacity, 1)

  // No backdrop (no track / no artwork) — the only case that unmounts.
  const idle = resolvePlasmaLayer(false, false)
  assert.equal(idle.mounted, false)
})

test("backdrop crossfade keeps the previous artwork under the next one", () => {
  assert.deepEqual(
    resolveBackdropLayers("/artwork/old", "/artwork/new"),
    {
      visibleUrl: "/artwork/new",
      fadingUrl: "/artwork/old",
    }
  )
})

test("artwork transitions wait until the next image is loaded and decoded", async () => {
  let decoded = false
  let resolved = false
  const image = {
    src: "",
    complete: false,
    naturalWidth: 0,
    onload: null as (() => void) | null,
    onerror: null as (() => void) | null,
    decode: async () => {
      decoded = true
    },
  }

  const pending = preloadArtworkImage(
    "/artwork/next",
    () => image
  ).then(() => {
    resolved = true
  })

  assert.equal(image.src, "/artwork/next")
  assert.equal(resolved, false)

  image.onload?.()
  await pending

  assert.equal(decoded, true)
  assert.equal(resolved, true)
})

test("plasma accepts the artwork theme RGB custom property", () => {
  assert.deepEqual(parsePlasmaColor("rgb(64 128 255)"), [
    64 / 255,
    128 / 255,
    1,
  ])
  assert.deepEqual(parsePlasmaColor("#ff2a6d"), [1, 42 / 255, 109 / 255])
})

test("plasma reads the current track accent from computed styles", () => {
  const originalDocument = globalThis.document
  const originalGetComputedStyle = globalThis.getComputedStyle

  globalThis.document = { documentElement: {} } as Document
  globalThis.getComputedStyle = (() =>
    ({
      getPropertyValue: (name: string) =>
        name === "--track-accent" ? "rgb(12 34 56)" : "",
    }) as CSSStyleDeclaration) as typeof getComputedStyle

  try {
    assert.deepEqual(readTrackAccentColor(), [12 / 255, 34 / 255, 56 / 255])
  } finally {
    globalThis.document = originalDocument
    globalThis.getComputedStyle = originalGetComputedStyle
  }
})

test("plasma can blend between two accent colors", () => {
  assert.deepEqual(
    mixPlasmaColor([0, 0.5, 1], [1, 0, 0], 0.25),
    [0.25, 0.375, 0.75]
  )
})

test("plasma uses the shared accent easing endpoints", () => {
  assert.equal(easeTrackAccentTransition(0), 0)
  assert.equal(easeTrackAccentTransition(1), 1)
  assert.ok(easeTrackAccentTransition(0.5) > 0.5)
})

test("plasma reads the shared accent transition duration", () => {
  const originalDocument = globalThis.document
  const originalGetComputedStyle = globalThis.getComputedStyle

  globalThis.document = { documentElement: {} } as Document
  globalThis.getComputedStyle = (() =>
    ({
      getPropertyValue: (name: string) =>
        name === "--track-accent-transition-duration" ? "450ms" : "",
    }) as CSSStyleDeclaration) as typeof getComputedStyle

  try {
    assert.equal(readTrackAccentTransitionDurationMs(), 450)
  } finally {
    globalThis.document = originalDocument
    globalThis.getComputedStyle = originalGetComputedStyle
  }
})
