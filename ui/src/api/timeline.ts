import { ApiError, apiFetch } from "./client"
import { decodeTimeline } from "@/engine/timeline/decoder"
import type { DecodedTimeline } from "@/engine/timeline/types"

export type TimelineAvailability = "loading" | "missing" | "queued" | "running" | "stale" | "failed" | "ready"
export interface TimelineLoadState {
  readonly status: TimelineAvailability
  readonly timeline?: DecodedTimeline
  readonly message?: string
}

const decodedByTrack = new Map<number, Promise<TimelineLoadState>>()

async function statusFor(trackId: number): Promise<TimelineLoadState> {
  const response = await apiFetch<{ items: Array<{ status: TimelineAvailability; error?: string | null }> }>(
    "/api/v1/timeline/status",
    { method: "POST", body: JSON.stringify({ track_ids: [trackId] }) },
  )
  const item = response.items[0]
  return { status: item?.status ?? "missing", message: item?.error ?? undefined }
}

async function fetchTimeline(trackId: number): Promise<TimelineLoadState> {
  try {
    const manifest = await apiFetch<unknown>(`/api/v1/tracks/${trackId}/timeline/manifest`)
    const response = await fetch(`/api/v1/tracks/${trackId}/timeline/payload`, { credentials: "same-origin" })
    if (!response.ok) throw new ApiError(response.status, "timeline_payload", `HTTP ${response.status}`)
    return { status: "ready", timeline: await decodeTimeline(manifest, await response.arrayBuffer()) }
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return statusFor(trackId)
    if (error instanceof ApiError && error.status === 409) return { status: "stale", message: error.message }
    return { status: "failed", message: error instanceof Error ? error.message : "Waveform unavailable" }
  }
}

export function loadTimeline(trackId: number): Promise<TimelineLoadState> {
  let pending = decodedByTrack.get(trackId)
  if (!pending) {
    pending = fetchTimeline(trackId)
    decodedByTrack.set(trackId, pending)
    void pending.then((result) => {
      if (result.status !== "ready" && decodedByTrack.get(trackId) === pending) decodedByTrack.delete(trackId)
    })
  }
  return pending
}

export function invalidateTimeline(trackId?: number): void {
  if (trackId === undefined) decodedByTrack.clear()
  else decodedByTrack.delete(trackId)
}
