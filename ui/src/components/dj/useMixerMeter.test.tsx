import { act, renderHook } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

const playback = vi.hoisted(() => ({ getMixerMeters: vi.fn() }))
vi.mock("@/engine/playback", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/engine/playback")>()),
  playerPlayback: playback,
}))

import { useMixerMeter } from "./useMixerMeter"

describe("useMixerMeter", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    let callback: FrameRequestCallback | null = null
    vi.stubGlobal("requestAnimationFrame", vi.fn((next: FrameRequestCallback) => {
      callback = next
      return 1
    }))
    vi.stubGlobal("cancelAnimationFrame", vi.fn())
    vi.stubGlobal("__runMeterFrame", (time: number) => callback?.(time))
    playback.getMixerMeters.mockReturnValue({ A: 0, B: 0, master: 0 })
  })

  it("updates a meter from the shared analyser clock", () => {
    const { result } = renderHook(() => useMixerMeter("A"))
    expect(result.current).toBe(0)

    playback.getMixerMeters.mockReturnValue({ A: 0.75, B: 0.2, master: 0.6 })
    act(() => { (globalThis as typeof globalThis & { __runMeterFrame(time: number): void }).__runMeterFrame(60) })

    expect(result.current).toBe(0.75)
  })
})
