import { describe, expect, it } from "vitest"
import {
  channelFaderGain,
  clampBipolar,
  clampNormalized,
  djCrossfaderGains,
  eqGainDb,
  filterFrequencies,
  parameterRampWindow,
  trimGain,
} from "./curves"

describe("playback parameter curves", () => {
  it("keeps both decks at unity in the centre and fades only toward an edge", () => {
    expect(djCrossfaderGains(-1)).toEqual({ A: 1, B: 0 })
    expect(djCrossfaderGains(-0.5)).toEqual({ A: 1, B: 0.5 })
    expect(djCrossfaderGains(0)).toEqual({ A: 1, B: 1 })
    expect(djCrossfaderGains(0.5)).toEqual({ A: 0.5, B: 1 })
    expect(djCrossfaderGains(1)).toEqual({ A: 0, B: 1 })
  })

  it("keeps the filter centre neutral and maps both directions", () => {
    expect(filterFrequencies(0)).toEqual({ lowpassHz: 20_000, highpassHz: 20 })
    expect(filterFrequencies(-1).lowpassHz).toBeCloseTo(20)
    expect(filterFrequencies(1).highpassHz).toBeCloseTo(20_000)
    expect(eqGainDb("mid", 0.5)).toBe(0)
    expect(eqGainDb("mid", 0)).toBe(-24)
    expect(eqGainDb("mid", 1)).toBe(6)
  })

  it("clamps public normalized and bipolar parameters", () => {
    expect(clampNormalized(-2)).toBe(0)
    expect(clampNormalized(3)).toBe(1)
    expect(clampBipolar(-2)).toBe(-1)
    expect(clampBipolar(3)).toBe(1)
    expect(channelFaderGain(0.5)).toBe(0.25)
    expect(trimGain(0.5)).toBeCloseTo(1)
  })

  it("maps requested audio time to a non-negative ramp window", () => {
    expect(parameterRampWindow(10, 8, 0.02)).toEqual({ startTime: 10, endTime: 10.02 })
    expect(parameterRampWindow(10, 12, 0.02)).toEqual({ startTime: 12, endTime: 12.02 })
    expect(parameterRampWindow(10, undefined, -1)).toEqual({ startTime: 10, endTime: 10 })
  })
})
