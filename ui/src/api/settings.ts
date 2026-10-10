import { apiFetch, apiUrl } from "./client"
import type { SupportedLanguage } from "@/i18n"

export interface UserSettings {
  language: SupportedLanguage
  transcoding_enabled: boolean
  transcoding_bitrate_kbps: TranscodingBitrate
  /** Upcoming queue tracks the player keeps downloaded ahead (1–PREFETCH_TRACKS_MAX). */
  prefetch_tracks: number
}

/** Mirrors the backend's PREFETCH_TRACKS_MAX (app/schemas/requests.py). */
export const PREFETCH_TRACKS_MAX = 5

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
