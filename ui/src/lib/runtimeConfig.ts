type CapacitorGlobal = typeof globalThis & {
  Capacitor?: {
    isNativePlatform(): boolean
  }
}

export function isNative(): boolean {
  // Capacitor injects itself as a global — no import needed
  return !!(globalThis as CapacitorGlobal).Capacitor?.isNativePlatform()
}
