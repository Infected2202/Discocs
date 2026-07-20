import { describe, expect, it } from "vitest"
import { classifyStretchFailure, detectStretchCapability } from "./capabilities"

describe("Signalsmith capability classification", () => {
  it("distinguishes missing worklet and WASM capabilities", () => {
    expect(detectStretchCapability({ AudioContext: class {} } as unknown as typeof globalThis)?.code)
      .toBe("audio-worklet-unavailable")
    expect(detectStretchCapability({ AudioContext: class {}, AudioWorkletNode: class {} } as unknown as typeof globalThis)?.code)
      .toBe("wasm-unavailable")
  })

  it("classifies CSP, WASM and generic initialization failures", () => {
    expect(classifyStretchFailure(new DOMException("blocked by policy", "SecurityError"))).toMatchObject({
      code: "worklet-blocked",
      recoverable: false,
    })
    expect(classifyStretchFailure(new WebAssembly.CompileError("bad wasm"))).toMatchObject({
      code: "wasm-unavailable",
      recoverable: false,
    })
    expect(classifyStretchFailure(new Error("node failed"))).toMatchObject({
      code: "initialization-failed",
      recoverable: true,
    })
  })
})
