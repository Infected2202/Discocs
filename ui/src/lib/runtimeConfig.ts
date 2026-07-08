const BACKEND_URL_KEY = "discocs_backend_url"

export const DEFAULT_BACKEND_URL = "http://192.168.1.146:8711"

type CapacitorGlobal = typeof globalThis & {
  Capacitor?: {
    isNativePlatform(): boolean
  }
}

export function isNative(): boolean {
  // Capacitor injects itself as a global — no import needed
  return !!(globalThis as CapacitorGlobal).Capacitor?.isNativePlatform()
}

let cachedBackendUrl: string | null = null

export async function getBackendUrl(): Promise<string> {
  if (cachedBackendUrl !== null) return cachedBackendUrl

  if (isNative()) {
    const { Preferences } = await import("@capacitor/preferences")
    const { value } = await Preferences.get({ key: BACKEND_URL_KEY })
    cachedBackendUrl = value ?? DEFAULT_BACKEND_URL
  } else {
    cachedBackendUrl = localStorage.getItem(BACKEND_URL_KEY) ?? ""
  }

  return cachedBackendUrl
}

export async function setBackendUrl(url: string): Promise<void> {
  const trimmed = url.trim()
  cachedBackendUrl = trimmed

  if (isNative()) {
    const { Preferences } = await import("@capacitor/preferences")
    if (trimmed) {
      await Preferences.set({ key: BACKEND_URL_KEY, value: trimmed })
    } else {
      await Preferences.remove({ key: BACKEND_URL_KEY })
    }
  } else if (trimmed) {
    localStorage.setItem(BACKEND_URL_KEY, trimmed)
  } else {
    localStorage.removeItem(BACKEND_URL_KEY)
  }
}

export function getBackendUrlSync(): string {
  if (!isNative()) return ""
  return cachedBackendUrl ?? DEFAULT_BACKEND_URL
}

export async function initBackendUrl(): Promise<string> {
  cachedBackendUrl = await getBackendUrl()
  return cachedBackendUrl
}
