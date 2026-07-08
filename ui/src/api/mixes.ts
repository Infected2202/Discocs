import { apiFetch, apiUrl } from "./client"
import type { GeneratedMixDetail, GeneratedMixSummary, PlaybackEnvelope } from "./types"

export interface MixListResponse {
  items: GeneratedMixSummary[]
  total: number
  limit: number
  offset: number
  next_offset: number | null
}

export function fetchMixes(limit = 50, offset = 0): Promise<MixListResponse> {
  return apiFetch(apiUrl("/api/v1/mixes", { limit, offset }))
}

export function fetchMix(id: string): Promise<GeneratedMixDetail> {
  return apiFetch(`/api/v1/mixes/${id}`)
}

export interface SaveMixPayload {
  title?: string
  description?: string | null
}

export function saveMix(id: string, payload?: SaveMixPayload): Promise<GeneratedMixSummary> {
  return apiFetch(`/api/v1/mixes/${id}/save`, {
    method: "POST",
    ...(payload ? { body: JSON.stringify(payload) } : {}),
  })
}

export function playMix(id: string): Promise<PlaybackEnvelope> {
  return apiFetch(`/api/v1/mixes/${id}/play`, { method: "POST" })
}
