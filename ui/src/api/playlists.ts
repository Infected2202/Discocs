import { ApiError, apiFetch, apiUrl } from "./client"
import type {
  PlaybackEnvelope,
  PlaylistDetail,
  PlaylistListResponse,
  PlaylistSummary,
  TrackSummary,
} from "./types"

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

// ----------------------------------------------------------------------------
// User playlists
// ----------------------------------------------------------------------------

export interface CreatePlaylistPayload {
  title: string
  description?: string | null
  visibility?: "public" | "private"
  track_ids?: number[]
}

export interface UpdatePlaylistPayload {
  title?: string
  description?: string | null
  visibility?: "public" | "private"
}

export function fetchPlaylists(params?: { limit?: number; offset?: number }): Promise<PlaylistListResponse> {
  return apiFetch(apiUrl("/api/v1/playlists", { limit: params?.limit, offset: params?.offset }))
}

export function createPlaylist(payload: CreatePlaylistPayload): Promise<PlaylistSummary> {
  return apiFetch("/api/v1/playlists", { method: "POST", body: JSON.stringify(payload) })
}

export function fetchPlaylist(playlistId: number): Promise<PlaylistDetail> {
  return apiFetch(`/api/v1/playlists/${playlistId}`)
}

export function updatePlaylist(playlistId: number, patch: UpdatePlaylistPayload): Promise<PlaylistSummary> {
  return apiFetch(`/api/v1/playlists/${playlistId}`, { method: "PATCH", body: JSON.stringify(patch) })
}

export async function deletePlaylist(playlistId: number): Promise<void> {
  // 204 No Content — apiFetch would choke on the empty body.
  const res = await fetch(`/api/v1/playlists/${playlistId}`, {
    method: "DELETE",
    credentials: "same-origin",
  })
  if (!res.ok) {
    let code = "api_error"
    let message = `HTTP ${res.status}`
    try {
      const body = await res.json()
      code = body?.error?.code ?? code
      message = body?.error?.message ?? message
    } catch {
      // ignore parse failure
    }
    throw new ApiError(res.status, code, message)
  }
}

export function addTracksToPlaylist(
  playlistId: number,
  trackIds: number[],
): Promise<{ added: number; track_count: number }> {
  return apiFetch(`/api/v1/playlists/${playlistId}/tracks`, {
    method: "POST",
    body: JSON.stringify({ track_ids: trackIds }),
  })
}

export function removePlaylistTracks(
  playlistId: number,
  trackIds: number[],
): Promise<{ removed: number; track_count: number }> {
  return apiFetch(`/api/v1/playlists/${playlistId}/tracks/remove`, {
    method: "POST",
    body: JSON.stringify({ track_ids: trackIds }),
  })
}

export function reorderPlaylistTracks(
  playlistId: number,
  trackIds: number[],
): Promise<{ track_ids: number[] }> {
  return apiFetch(`/api/v1/playlists/${playlistId}/tracks/reorder`, {
    method: "POST",
    body: JSON.stringify({ track_ids: trackIds }),
  })
}

export function playPlaylist(playlistId: number): Promise<PlaybackEnvelope> {
  return apiFetch(`/api/v1/playlists/${playlistId}/play`, { method: "POST" })
}
