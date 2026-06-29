import { apiFetch, apiUrl } from "./client"
import type { PlaybackSession, PlaybackQueue } from "./types"

export interface FlowProfile {
  available: boolean
  status: string
  model_key: string
  profile_id?: string
  region_count: number
  last_built_at?: string | null
  regions: FlowRegionSummary[]
}

export interface FlowRegionSummary {
  id: string
  region_index: number
  weight: number
  seed_count: number
  candidate_count: number
  medoid_track_id?: number | null
}

export interface FlowStartResponse {
  session: PlaybackSession
  queue: {
    items: PlaybackQueue["items"]
    visible_buffer: number
  }
  flow: {
    profile_id: string
    active_region_id: string
    generation_run_id: string
    active_region: FlowRegionSummary
  }
}

export async function getFlowProfile(modelKey = "discogs_multi"): Promise<FlowProfile> {
  return apiFetch(apiUrl("/api/v1/flow/profile", { model_key: modelKey }))
}

export async function startFlow(settings?: Record<string, unknown>): Promise<FlowStartResponse> {
  return apiFetch("/api/v1/flow/start", {
    method: "POST",
    body: JSON.stringify({ settings: settings ?? {} }),
  })
}

export interface FlowRefillParams {
  session_id: string
  visible_buffer?: number
  region_id?: string
  include_debug?: boolean
}

export interface FlowEventParams {
  session_id: string
  event_type: string
  track_id?: number | null
  artist_id?: number | null
  release_id?: number | null
}

export function flowRefill(params: FlowRefillParams): Promise<unknown> {
  return apiFetch("/api/v1/flow/refill", {
    method: "POST",
    body: JSON.stringify({ visible_buffer: 5, ...params }),
  })
}

export function flowEvent(params: FlowEventParams): Promise<unknown> {
  return apiFetch("/api/v1/flow/event", {
    method: "POST",
    body: JSON.stringify(params),
  })
}
