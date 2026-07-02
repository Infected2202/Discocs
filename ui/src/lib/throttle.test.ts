import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { throttle } from "./throttle"

describe("throttle", () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it("fires immediately on the leading edge", () => {
    const fn = vi.fn()
    const t = throttle(fn, 250)
    t("a")
    expect(fn).toHaveBeenCalledTimes(1)
    expect(fn).toHaveBeenLastCalledWith("a")
  })

  it("suppresses calls within the interval", () => {
    const fn = vi.fn()
    const t = throttle(fn, 250)
    t("a")
    t("b")
    t("c")
    // Only the leading call has run so far
    expect(fn).toHaveBeenCalledTimes(1)
    expect(fn).toHaveBeenLastCalledWith("a")
  })

  it("delivers the latest suppressed value on the trailing edge", () => {
    const fn = vi.fn()
    const t = throttle(fn, 250)
    t("a") // leading
    t("b")
    t("c") // latest pending
    vi.advanceTimersByTime(250)
    // Trailing fires once, with the most recent args — 'b' must be dropped
    expect(fn).toHaveBeenCalledTimes(2)
    expect(fn).toHaveBeenLastCalledWith("c")
  })

  it("caps a high-frequency stream to one call per interval", () => {
    const fn = vi.fn()
    const t = throttle(fn, 250)
    // Simulate 60Hz: a call every ~16ms for 1s → 60 calls
    for (let i = 0; i < 60; i++) {
      t(i)
      vi.advanceTimersByTime(16)
    }
    // 1s at 250ms cap ≈ 4-5 calls, nowhere near 60
    expect(fn.mock.calls.length).toBeGreaterThanOrEqual(4)
    expect(fn.mock.calls.length).toBeLessThanOrEqual(6)
  })

  it("cancel() drops a pending trailing call", () => {
    const fn = vi.fn()
    const t = throttle(fn, 250)
    t("a") // leading
    t("b") // pending trailing
    t.cancel()
    vi.advanceTimersByTime(250)
    expect(fn).toHaveBeenCalledTimes(1)
    expect(fn).toHaveBeenLastCalledWith("a")
  })
})
