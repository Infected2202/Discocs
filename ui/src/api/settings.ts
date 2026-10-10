import { apiFetch, apiUrl } from "./client"
import type { SupportedLanguage } from "@/i18n"

export interface UserSettings {
  language: SupportedLanguage
  transcoding_enabled: boolean
  transcoding_bitrate_kbps: TranscodingBitrate
  /** Off (default): nothing is downloaded ahead, tracks just stream. */
  prefetch_ahead_enabled: boolean
  /** Upcoming tracks kept downloaded while prefetch_ahead_enabled is on (PREFETCH_TRACKS_MIN–MAX). */
  prefetch_tracks: number
}

/** Mirror the backend's limits (app/store/settings.py). */
export const PREFETCH_TRACKS_MIN = 1
export const PREFETCH_TRACKS_MAX = 5

/** Tracks the player keeps downloaded ahead: none unless loading ahead is switched on. */
export function prefetchTrackCount(settings: UserSettings): number {
  return settings.prefetch_ahead_enabled ? settings.prefetch_tracks : 0
}

export type TranscodingBitrate = 96 | 128 | 192 | 256 | 320

export interface PlaybackProfile {
  transcodingEnabled: boolean
  bitrateKbps: TranscodingBitrate
  key: string
}

export function playbackProfile(settings: UserSettings): PlaybackProfile {
  return {
    transcodingEnabled: settings.transcoding_enabled,
    bitrateKbps: settings.transcoding_bitrate_kbps,
    key: settings.transcoding_enabled ? `mp3-${settings.transcoding_bitrate_kbps}` : "raw",
  }
}

export async function getUserSettings(): Promise<UserSettings> {
  return apiFetch<UserSettings>(apiUrl("/api/v1/me/settings"))
}

export async function updateUserSettings(
  patch: Partial<UserSettings>
): Promise<UserSettings> {
  return apiFetch<UserSettings>(apiUrl("/api/v1/me/settings"), {
    method: "PATCH",
    body: JSON.stringify(patch),
  })
}
