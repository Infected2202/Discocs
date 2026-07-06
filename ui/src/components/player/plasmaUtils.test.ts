import { describe, expect, it } from "vitest"
import {
  PLASMA_FRAME_INTERVAL_MS,
  parsePlasmaColor,
  shouldAdvancePlasmaFrame,
} from "./plasmaUtils"

describe("shouldAdvancePlasmaFrame (30fps cap)", () => {
  it("skips frames that arrive sooner than the interval", () => {
    // rAF at ~60fps means ~16.7ms between calls — half of them must be skipped
    expect(shouldAdvancePlasmaFrame(16.7, 0)).toBe(false)
  })

  it("advances once enough time has elapsed", () => {
    expect(shouldAdvancePlasmaFrame(33.4, 0)).toBe(true)
  })

  it("treats an exactly-on-interval frame as ready (inclusive threshold)", () => {
    expect(shouldAdvancePlasmaFrame(PLASMA_FRAME_INTERVAL_MS, 0)).toBe(true)
  })

  it("measures from the last rendered frame, not from zero", () => {
    // 40ms is plenty since epoch, but only 5ms since the last render → skip
    expect(shouldAdvancePlasmaFrame(1005, 1000)).toBe(false)
    // 40ms since the last render → advance
    expect(shouldAdvancePlasmaFrame(1040, 1000)).toBe(true)
  })

  it("caps at roughly 30fps", () => {
    // Interval must be ~33ms; if it regressed to 60fps (16.7) this fails
    expect(PLASMA_FRAME_INTERVAL_MS).toBeCloseTo(33.33, 1)
  })
})

describe("parsePlasmaColor", () => {
  it("parses hex colors", () => {
    expect(parsePlasmaColor("#ff2a6d")).toEqual([1, 42 / 255, 109 / 255])
  })

  it("parses comma- and space-separated rgb() values", () => {
    expect(parsePlasmaColor("rgb(255, 0, 128)")).toEqual([1, 0, 128 / 255])
    expect(parsePlasmaColor("rgb(255 0 128)")).toEqual([1, 0, 128 / 255])
    expect(parsePlasmaColor("rgba( 255 , 0 , 128 , 0.5)")).toEqual([1, 0, 128 / 255])
  })

  it("falls back for unrecognized input", () => {
    expect(parsePlasmaColor("not-a-color")).toEqual([1, 0.165, 0.427])
  })

  it("handles a whitespace flood without hanging", () => {
    // Regression for S5852: the old `\s*[, ]\s*` pattern had two adjacent
    // optional whitespace matches around the separator, letting
    // backtracking split the same whitespace run many ways once no digit
    // followed — polynomial blowup on a string of just spaces.
    const pathological = `rgba(1${" ".repeat(50_000)}`
    const started = performance.now()
    parsePlasmaColor(pathological)
    expect(performance.now() - started).toBeLessThan(1000)
  })
})
