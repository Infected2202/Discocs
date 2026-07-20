import { renderHook, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { TimelineLoadState } from "@/api/timeline"

const loadTimeline = vi.hoisted(() => vi.fn())
vi.mock("@/api/timeline", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/timeline")>()),
  loadTimeline,
}))

import { useTimeline } from "./useTimeline"

describe("useTimeline", () => {
  beforeEach(() => loadTimeline.mockReset())

  it("loads an enabled track and returns to idle when disabled", async () => {
    const ready: TimelineLoadState = { status: "ready", timeline: { durationSeconds: 1, levels: [] } }
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
})
