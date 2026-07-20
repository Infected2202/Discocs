import { describe, expect, it } from "vitest"
import { pointerXToTime, resolveFollowWindow, selectWaveformLevel } from "./geometry"
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

  it("keeps a follow window centred until it reaches track edges", () => {
    expect(resolveFollowWindow(240, 120, 20)).toEqual({ startSeconds: 110, endSeconds: 130 })
    expect(resolveFollowWindow(240, 238, 20)).toEqual({ startSeconds: 220, endSeconds: 240 })
  })
})
