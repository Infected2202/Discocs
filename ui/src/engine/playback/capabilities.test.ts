import { describe, expect, it } from "vitest"
import { detectPlaybackCapabilities } from "./capabilities"

describe("detectPlaybackCapabilities", () => {
  it("enables manual mixing only when the required Web Audio seam exists", () => {
    class SupportedContext {
      createMediaElementSource() {}
    }
    const supported = {
      Audio: function Audio() {},
      AudioContext: SupportedContext,
    } as unknown as typeof globalThis
    expect(detectPlaybackCapabilities(supported)).toMatchObject({
      ordinary: true,
      webAudio: true,
      mediaElementSource: true,
      manualMix: true,
      reasons: [],
    })

    const unsupported = { Audio: function Audio() {} } as unknown as typeof globalThis
    expect(detectPlaybackCapabilities(unsupported)).toMatchObject({
      ordinary: true,
      manualMix: false,
      reasons: ["AudioContext is unavailable"],
    })
  })
})
