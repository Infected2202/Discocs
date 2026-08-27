import { act, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { useVisualViewportFit } from "./useVisualViewportFit"

interface FakeViewport {
  height: number
  offsetTop: number
  addEventListener: ReturnType<typeof vi.fn>
  removeEventListener: ReturnType<typeof vi.fn>
}

const listeners = new Map<string, () => void>()

function stubViewport(height: number, offsetTop = 0): FakeViewport {
  const viewport: FakeViewport = {
    height,
    offsetTop,
    addEventListener: vi.fn((event: string, handler: () => void) => listeners.set(event, handler)),
    removeEventListener: vi.fn((event: string) => listeners.delete(event)),
  }
  Object.defineProperty(globalThis, "visualViewport", {
    value: viewport,
    configurable: true,
    writable: true,
  })
  return viewport
}

function stubLayoutHeight(height: number) {
  Object.defineProperty(document.documentElement, "clientHeight", {
    value: height,
    configurable: true,
  })
}

function clearViewport() {
  listeners.clear()
  Object.defineProperty(globalThis, "visualViewport", {
    value: undefined,
    configurable: true,
    writable: true,
  })
}

// jsdom does not implement visualViewport, but a future version might; pin the
// starting state so "no API" is genuinely tested rather than assumed.
beforeEach(clearViewport)
afterEach(clearViewport)

describe("useVisualViewportFit", () => {
  it("stays inert on a browser without a visual viewport", () => {
    const { result } = renderHook(() => useVisualViewportFit(true))

    expect(result.current).toEqual({ offset: 0, maxHeight: null })
  })

  it("stays inert while the overlay is closed", () => {
    stubViewport(400)
    stubLayoutHeight(800)

    const { result } = renderHook(() => useVisualViewportFit(false))

    expect(result.current).toEqual({ offset: 0, maxHeight: null })
    expect(listeners.size).toBe(0)
  })

  it("does not move an overlay when the two viewports agree", () => {
    stubViewport(800)
    stubLayoutHeight(800)

    const { result } = renderHook(() => useVisualViewportFit(true))

    expect(result.current.offset).toBe(0)
  })

  it("lifts the overlay out from behind an on-screen keyboard", () => {
    // A 300px keyboard leaves 500 of an 800px page visible, so the overlay's
    // centre has to rise by 150 to sit in the middle of what is still shown.
    stubViewport(500)
    stubLayoutHeight(800)

    const { result } = renderHook(() => useVisualViewportFit(true))

    expect(result.current.offset).toBe(-150)
    expect(result.current.maxHeight).toBe(468)
  })

  it("follows a viewport panned away from the top of the page", () => {
    stubViewport(500, 100)
    stubLayoutHeight(800)

    const { result } = renderHook(() => useVisualViewportFit(true))

    expect(result.current.offset).toBe(-50)
  })

  it("recomputes when the keyboard opens and closes", () => {
    const viewport = stubViewport(800)
    stubLayoutHeight(800)
    const { result } = renderHook(() => useVisualViewportFit(true))

    expect(result.current.offset).toBe(0)

    viewport.height = 500
    act(() => listeners.get("resize")?.())
    expect(result.current.offset).toBe(-150)

    viewport.height = 800
    act(() => listeners.get("resize")?.())
    expect(result.current.offset).toBe(0)
  })

  it("detaches its listeners when the overlay goes away", () => {
    const viewport = stubViewport(500)
    stubLayoutHeight(800)

    const { unmount } = renderHook(() => useVisualViewportFit(true))
    expect(viewport.addEventListener).toHaveBeenCalledTimes(2)

    unmount()

    expect(viewport.removeEventListener).toHaveBeenCalledTimes(2)
    expect(listeners.size).toBe(0)
  })
})
