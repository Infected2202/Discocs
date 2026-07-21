import { describe, expect, it } from "vitest"
import { timelineSupportsStretch } from "./selection"
import type { DecodedTimeline } from "@/engine/timeline/types"

const timeline = (overrides: Partial<DecodedTimeline> = {}): DecodedTimeline => ({
  durationSeconds: 180,
  levels: [],
  bpm: 120,
  beatConfidence: 0.8,
  rhythmCoverageSeconds: 180,
  beats: new Float32Array([0.5, 1]),
  localTempo: new Float32Array([120, 120]),
  ...overrides,
})

describe("Signalsmith timeline selection", () => {
  it("requires aligned beat and local-tempo observations", () => {
    expect(timelineSupportsStretch(timeline())).toEqual({ ready: true, reason: null })
    expect(timelineSupportsStretch(timeline({ beats: new Float32Array([0.5]) }))).toEqual({
      ready: false,
      reason: "Beat timeline is unavailable",
    })
    expect(timelineSupportsStretch(timeline({ localTempo: new Float32Array([120]) }))).toEqual({
      ready: false,
      reason: "Beat timeline is unavailable",
    })
  })
})
