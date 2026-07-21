import { describe, expect, it } from "vitest"
import { alignFollowerPosition, locateBeat, projectPlayhead, tempoRatioFor } from "./beatSync"

describe("beat sync mapping", () => {
  it("maps a master beat phase to the closest follower interval", () => {
    const master = new Float32Array([0, 0.5, 1, 1.5])
    const follower = new Float32Array([0.1, 0.7, 1.3, 1.9])

    expect(locateBeat(master, 0.75)).toEqual({ interval: 1, phase: 0.5 })
    expect(alignFollowerPosition(master, 0.75, follower, 1.5)).toBeCloseTo(1.6)
  })

  it("rejects tempo matches outside the approved eight-percent pitch range", () => {
    expect(tempoRatioFor(126, 120)).toBeCloseTo(1.05)
    expect(tempoRatioFor(140, 120)).toBeNull()
    expect(tempoRatioFor(0, 120)).toBeNull()
  })

  it("projects a playing anchor to a common scheduled audio time", () => {
    expect(projectPlayhead(12, 5, 1.05, 7)).toBeCloseTo(14.1)
    expect(projectPlayhead(12, 5, 0, 7)).toBe(12)
  })
})
