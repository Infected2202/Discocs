import { Capacitor } from "@capacitor/core"
import { Preferences } from "@capacitor/preferences"

const BACKEND_URL_KEY = "discocs_backend_url"

/**
 * Default backend URL used on first launch or when user has not configured one.
 * LAN IP matches the value in AGENTS.md.
 */
export const DEFAULT_BACKEND_URL = "http://192.168.1.146:8711"

export function isNative(): boolean {
  return Capacitor.isNativePlatform()
}

let cachedBackendUrl: string | null = null

export async function getBackendUrl(): Promise<string> {
  if (cachedBackendUrl) return cachedBackendUrl

  if (isNative()) {
    const { value } = await Preferences.get({ key: BACKEND_URL_KEY })
    cachedBackendUrl = value || DEFAULT_BACKEND_URL
  } else {
    cachedBackendUrl = localStorage.getItem(BACKEND_URL_KEY) || ""
  }

  return cachedBackendUrl
}

export async function setBackendUrl(url: string): Promise<void> {
  const trimmed = url.trim()
  cachedBackendUrl = trimmed || ""

  if (isNative()) {
    if (trimmed) {
      await Preferences.set({ key: BACKEND_URL_KEY, value: trimmed })
    } else {
      await Preferences.remove({ key: BACKEND_URL_KEY })
    }
  } else {
    if (trimmed) {
      localStorage.setItem(BACKEND_URL_KEY, trimmed)
    } else {
      localStorage.removeItem(BACKEND_URL_KEY)
    }
  }
}

/**
 * Returns a synchronously-available backend URL.
 * On web, returns "" (empty) — meaning "same origin" (proxy via Vite).
 * On native, returns the cached value or the default — never empty.
 */
export function getBackendUrlSync(): string {
  if (!isNative()) return ""
  return cachedBackendUrl ?? DEFAULT_BACKEND_URL
}

/**
 * Preload backend URL into cache so getBackendUrlSync works on native.
 * Call once at startup before any API request.
 */
export async function initBackendUrl(): Promise<string> {
  cachedBackendUrl = await getBackendUrl()
  return cachedBackendUrl
}
