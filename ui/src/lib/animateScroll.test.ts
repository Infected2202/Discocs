import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { animateScroll } from "./animateScroll"

describe("animateScroll", () => {
  let now = 0
  let frameCallback: FrameRequestCallback | undefined
  let frameId = 0

  beforeEach(() => {
    now = 1_000
    frameCallback = undefined
    frameId = 0

    vi.spyOn(performance, "now").mockImplementation(() => now)
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      frameCallback = callback
      frameId += 1
      return frameId
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  function runFrame(elapsedMs: number) {
    const callback = frameCallback
    expect(callback).toBeDefined()
    frameCallback = undefined
    callback?.(now + elapsedMs)
  }

  it("animates vertical scroll and calls onDone at the final frame", () => {
    const el = document.createElement("div")
    const onDone = vi.fn()

    animateScroll(el, 10, 110, 100, "y", onDone)

    runFrame(50)
    expect(el.scrollTop).toBeGreaterThan(10)
    expect(el.scrollTop).toBeLessThan(110)
    expect(onDone).not.toHaveBeenCalled()

    runFrame(100)
    expect(el.scrollTop).toBe(110)
    expect(onDone).toHaveBeenCalledTimes(1)
  })

  it("animates horizontal scroll without touching scrollTop", () => {
    const el = document.createElement("div")
    el.scrollTop = 25

    animateScroll(el, 0, 80, 100, "x")

    runFrame(100)
    expect(el.scrollLeft).toBe(80)
    expect(el.scrollTop).toBe(25)
  })
})
