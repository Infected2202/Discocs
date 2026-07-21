import { describe, expect, it } from "vitest"
import { pointerXToTime, resolveFollowWindow, selectWaveformLevel, tapeDragTime } from "./geometry"
import type { WaveformLevel } from "./types"

function level(bucketDurationSeconds: number): WaveformLevel {
  return {
    bucketDurationSeconds,
    minimum: new Int16Array(10),
    maximum: new Int16Array(10),
    low: new Uint16Array(10),
    mid: new Uint16Array(10),
    high: new Uint16Array(10),
  }
}

describe("waveform viewport geometry", () => {
  it("selects the coarsest level that still supplies one bucket per pixel", () => {
    const levels = [level(0.01), level(0.04), level(0.16)]

    expect(selectWaveformLevel(levels, { width: 1_000, startSeconds: 0, endSeconds: 50 }))
      .toBe(levels[1])
    expect(selectWaveformLevel(levels, { width: 1_000, startSeconds: 0, endSeconds: 8 }))
      .toBe(levels[0])
  })

  it("clamps pointer positions and converts them into timeline seconds", () => {
    const viewport = { width: 1_000, startSeconds: 40, endSeconds: 60 }

    expect(pointerXToTime(250, viewport)).toBe(45)
    expect(pointerXToTime(-50, viewport)).toBe(40)
    expect(pointerXToTime(1_500, viewport)).toBe(60)
  })

  it("keeps the playhead centred and leaves empty tape outside the track", () => {
    expect(resolveFollowWindow(120, 20)).toEqual({ startSeconds: 110, endSeconds: 130 })
    expect(resolveFollowWindow(0, 20)).toEqual({ startSeconds: -10, endSeconds: 10 })
    expect(resolveFollowWindow(240, 20)).toEqual({ startSeconds: 230, endSeconds: 250 })
  })

  it("maps tape drag distance around the fixed playhead and clamps to the track", () => {
    const viewport = { width: 1_000, startSeconds: 40, endSeconds: 60 }

    expect(tapeDragTime(50, -250, viewport, 240)).toBe(55)
    expect(tapeDragTime(2, 250, viewport, 240)).toBe(0)
    expect(tapeDragTime(238, -250, viewport, 240)).toBe(240)
  })
})
