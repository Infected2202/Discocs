import { apiFetch } from "./client"
import type { NavidromePingResponse, NavidromeSettings } from "./types"

export function fetchNavidromeSettings(): Promise<NavidromeSettings> {
  return apiFetch("/settings/navidrome")
}

export interface NavidromeSettingsPayload {
  url: string
  user: string
  password?: string
  auth_mode?: string
  timeout_seconds?: number
  download_mode?: string
  temp_dir?: string
}

export function saveNavidromeSettings(data: NavidromeSettingsPayload): Promise<NavidromeSettings> {
  return apiFetch("/settings/navidrome", {
    method: "PUT",
    body: JSON.stringify(data),
  })
}

export function pingNavidrome(): Promise<NavidromePingResponse> {
  return apiFetch("/navidrome/ping", { method: "POST" })
}
