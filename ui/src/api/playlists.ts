import { apiFetch } from "./client"
import type { PlaybackEnvelope, TrackSummary } from "./types"

export interface LikesPlaylist {
  id: string
  title: string
  subtitle: string
  track_count: number
  tracks: TrackSummary[]
}

export function fetchLikesPlaylist(): Promise<LikesPlaylist> {
  return apiFetch("/api/v1/playlists/likes")
}

export function playLikes(): Promise<PlaybackEnvelope> {
  return apiFetch("/api/v1/playlists/likes/play", { method: "POST" })
}
