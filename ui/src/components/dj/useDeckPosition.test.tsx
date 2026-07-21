import { act, renderHook } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { DeckSnapshot } from "@/engine/playback"

const playback = vi.hoisted(() => ({ getDeckCurrentTime: vi.fn() }))
vi.mock("@/engine/playback", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/engine/playback")>()),
  playerPlayback: playback,
}))

import { useDeckPosition } from "./useDeckPosition"

const deck = (transport: DeckSnapshot["transport"]): DeckSnapshot => ({
  id: "A",
  role: "program",
  preparation: "ready",
  transport,
  trackId: 1,
  queueItemId: "q1",
  duration: 180,
  anchor: { mediaSeconds: 12, audioTime: 1, rate: 1 },
  buffered: [],
})

describe("useDeckPosition", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    let callback: FrameRequestCallback | null = null
    vi.stubGlobal("requestAnimationFrame", vi.fn((next: FrameRequestCallback) => {
      callback = next
      return 1
    }))
    vi.stubGlobal("cancelAnimationFrame", vi.fn())
    playback.getDeckCurrentTime.mockReturnValue(12)
    vi.stubGlobal("__runDeckFrame", (time: number) => callback?.(time))
  })

  it("samples the physical deck clock while playing without rerendering the whole DJ surface", () => {
    const { result } = renderHook(() => useDeckPosition(deck("playing")))
    expect(result.current).toBe(12)

    playback.getDeckCurrentTime.mockReturnValue(13.25)
    act(() => { (globalThis as typeof globalThis & { __runDeckFrame(time: number): void }).__runDeckFrame(40) })

    expect(result.current).toBe(13.25)
  })

  it("uses the stable anchor and does not start a frame loop while paused", () => {
    const { result } = renderHook(() => useDeckPosition(deck("paused")))

    expect(result.current).toBe(12)
    expect(requestAnimationFrame).not.toHaveBeenCalled()
  })
})
