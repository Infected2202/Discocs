import { loadTimeline } from "@/api/timeline"
import type { DecodedTimeline } from "@/engine/timeline/types"
import { detectStretchCapability } from "./capabilities"

export interface StretchEligibility {
  readonly ready: boolean
  readonly reason: string | null
  readonly timeline?: DecodedTimeline
}

export type StretchEligibilityResolver = (trackId: number) => Promise<StretchEligibility>

export function timelineSupportsStretch(timeline: DecodedTimeline): StretchEligibility {
  if (timeline.beats.length < 2 || timeline.localTempo.length !== timeline.beats.length) {
    return { ready: false, reason: "Beat timeline is unavailable" }
  }
  if (!(timeline.rhythmCoverageSeconds > 0) || timeline.rhythmCoverageSeconds > timeline.durationSeconds) {
    return { ready: false, reason: "Beat timeline coverage is invalid" }
  }
  return { ready: true, reason: null }
}

export const resolveStretchEligibility: StretchEligibilityResolver = async (trackId) => {
  const capabilityFailure = detectStretchCapability()
  if (capabilityFailure) return { ready: false, reason: capabilityFailure.message }
  const state = await loadTimeline(trackId)
  if (state.status !== "ready" || !state.timeline) {
    return { ready: false, reason: state.message ?? `Timeline is ${state.status}` }
  }
  const eligibility = timelineSupportsStretch(state.timeline)
  return eligibility.ready ? { ...eligibility, timeline: state.timeline } : eligibility
}
