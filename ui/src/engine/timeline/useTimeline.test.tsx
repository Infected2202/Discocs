import { act, renderHook, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { TimelineLoadState } from "@/api/timeline"

const loadTimeline = vi.hoisted(() => vi.fn())
vi.mock("@/api/timeline", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/timeline")>()),
  loadTimeline,
}))

import { useTimeline } from "./useTimeline"

describe("useTimeline", () => {
  beforeEach(() => loadTimeline.mockReset())
  afterEach(() => vi.useRealTimers())

  it("loads an enabled track and returns to idle when disabled", async () => {
    const ready: TimelineLoadState = { status: "ready", timeline: {
      durationSeconds: 1, levels: [], bpm: 120, beatConfidence: 0.8, rhythmCoverageSeconds: 1,
      beats: new Float32Array(), localTempo: new Float32Array(),
    } }
    loadTimeline.mockResolvedValue(ready)
    const { result, rerender } = renderHook(({ trackId, enabled }) => useTimeline(trackId, enabled), {
      initialProps: { trackId: 7 as number | null, enabled: true },
    })
    expect(result.current.status).toBe("loading")
    await waitFor(() => expect(result.current).toBe(ready))
    rerender({ trackId: 7, enabled: false })
    expect(result.current.status).toBe("missing")
  })

  it("ignores a result that resolves after the track changed", async () => {
    let resolve!: (value: TimelineLoadState) => void
    loadTimeline.mockReturnValue(new Promise<TimelineLoadState>((done) => { resolve = done }))
    const { result, rerender } = renderHook(({ trackId }) => useTimeline(trackId), {
      initialProps: { trackId: 9 as number | null },
    })
    rerender({ trackId: null })
    resolve({ status: "failed", message: "late" })
    await Promise.resolve()
    expect(result.current.status).toBe("missing")
  })

  it("polls queued analysis until the admin job publishes a ready artifact", async () => {
    vi.useFakeTimers()
    const ready: TimelineLoadState = { status: "ready", timeline: {
      durationSeconds: 1, levels: [], bpm: 120, beatConfidence: 0.8, rhythmCoverageSeconds: 1,
      beats: new Float32Array(), localTempo: new Float32Array(),
    } }
    loadTimeline
      .mockResolvedValueOnce({ status: "queued" } satisfies TimelineLoadState)
      .mockResolvedValueOnce(ready)
    const { result, unmount } = renderHook(() => useTimeline(11))
    await act(async () => { await Promise.resolve() })
    expect(result.current.status).toBe("queued")

    await act(async () => { await vi.advanceTimersByTimeAsync(2_000) })

    expect(result.current).toBe(ready)
    expect(loadTimeline).toHaveBeenCalledTimes(2)
    unmount()
  })
})
