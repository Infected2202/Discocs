import { apiFetch, apiUrl } from "./client"
import type { AutoplayRefillResponse, PlaybackEnvelope, PlaybackEventResponse } from "./types"

export interface CreateSessionParams {
  source_type: string
  source_id?: number
  source_label: string
  mode?: string
  preferred_track_id?: number
  autoplay_enabled?: boolean
  shuffle_enabled?: boolean
  repeat_mode?: string
  settings?: Record<string, unknown>
  state?: Record<string, unknown>
}

export function createSession(params: CreateSessionParams): Promise<PlaybackEnvelope> {
  return apiFetch("/api/v1/playback/sessions", {
    method: "POST",
    body: JSON.stringify({
      mode: "linear",
      autoplay_enabled: true,
      shuffle_enabled: false,
      repeat_mode: "off",
      ...params,
    }),
  })
}

export function fetchSession(sessionId: string): Promise<PlaybackEnvelope> {
  return apiFetch(`/api/v1/playback/sessions/${sessionId}`)
}

export function fetchQueue(sessionId: string): Promise<PlaybackEnvelope> {
  return apiFetch(`/api/v1/playback/sessions/${sessionId}/queue`)
}

export interface SessionPatch {
  status?: string
  current_track_id?: number | null
  current_queue_item_id?: string | null
  autoplay_enabled?: boolean
  shuffle_enabled?: boolean
  repeat_mode?: string
  settings?: Record<string, unknown>
  state?: Record<string, unknown>
}

export function patchSession(sessionId: string, patch: SessionPatch): Promise<PlaybackEnvelope> {
  return apiFetch(`/api/v1/playback/sessions/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  })
}

export type QueueOperation = "replace" | "add" | "remove" | "move" | "jump" | "mark_current" | "handover"

export interface QueuePatch {
  operation: QueueOperation
  track_id?: number
  track_ids?: number[]
  queue_item_id?: string
  client_handover_id?: string
  position?: number
}

export function patchQueue(sessionId: string, patch: QueuePatch): Promise<PlaybackEnvelope> {
  return apiFetch(`/api/v1/playback/sessions/${sessionId}/queue`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  })
}

export interface PlaybackEventParams {
  session_id: string
  queue_item_id?: string
  track_id?: number
  release_id?: number
  artist_id?: number
  event_type: string
  position_seconds?: number
  duration_seconds?: number
  play_fraction?: number
  client_event_id?: string
  source?: string
  payload?: Record<string, unknown>
}

export function postEvent(params: PlaybackEventParams): Promise<PlaybackEventResponse> {
  return apiFetch("/api/v1/playback/events", {
    method: "POST",
    body: JSON.stringify({ source: "web", ...params }),
  })
}

export interface RefillParams {
  session_id: string
  visible_buffer?: number
  candidate_count?: number
  settings?: Record<string, unknown>
}

export function refillAutoplay(params: RefillParams): Promise<AutoplayRefillResponse> {
  return apiFetch(apiUrl("/api/v1/autoplay/refill"), {
    method: "POST",
    body: JSON.stringify(params),
  })
}

export function trackAudioUrl(trackId: number, profileKey = "raw"): string {
  return `/api/v1/tracks/${trackId}/audio?profile=${encodeURIComponent(profileKey)}`
}
