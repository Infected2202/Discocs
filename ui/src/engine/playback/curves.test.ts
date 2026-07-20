import { describe, expect, it } from "vitest"
import {
  channelFaderGain,
  clampBipolar,
  clampNormalized,
  equalPowerCrossfader,
  eqGainDb,
  filterFrequencies,
  parameterRampWindow,
  trimGain,
} from "./curves"

describe("playback parameter curves", () => {
  it("maps equal-power endpoints and centre", () => {
    expect(equalPowerCrossfader(-1)).toEqual({ A: 1, B: 0 })
    expect(equalPowerCrossfader(0).A).toBeCloseTo(Math.SQRT1_2)
    expect(equalPowerCrossfader(0).B).toBeCloseTo(Math.SQRT1_2)
    expect(equalPowerCrossfader(1).A).toBeCloseTo(0)
    expect(equalPowerCrossfader(1).B).toBe(1)
  })

  it("keeps the filter centre neutral and maps both directions", () => {
    expect(filterFrequencies(0)).toEqual({ lowpassHz: 20_000, highpassHz: 20 })
    expect(filterFrequencies(-1).lowpassHz).toBeCloseTo(20)
    expect(filterFrequencies(1).highpassHz).toBeCloseTo(20_000)
    expect(eqGainDb("mid", 0.8)).toBeCloseTo(0)
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
