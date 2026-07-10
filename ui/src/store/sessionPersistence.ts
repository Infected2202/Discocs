const SESSION_KEY = "discocs.sessionId.v1"
const POSITION_KEY = "discocs.playbackPosition.v1"

export interface PersistedPosition {
  sessionId: string
  queueItemId: string
  trackId: number
  seconds: number
}

export function persistSessionId(sessionId: string) {
  try { localStorage.setItem(SESSION_KEY, sessionId) } catch {}
}

export function loadPersistedSessionId(): string | null {
  try { return localStorage.getItem(SESSION_KEY) } catch { return null }
}

export function clearPersistedSessionId() {
  try { localStorage.removeItem(SESSION_KEY) } catch {}
}

// Позиция воспроизведения переживает выгрузку вкладки мобильным браузером:
// restoreSession после перезагрузки возвращает не только очередь, но и место
// в треке, вместо старта с нуля.
export function persistPlaybackPosition(position: PersistedPosition) {
  try { localStorage.setItem(POSITION_KEY, JSON.stringify(position)) } catch {}
}

export function loadPersistedPlaybackPosition(): PersistedPosition | null {
  try {
    const raw = localStorage.getItem(POSITION_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<Record<keyof PersistedPosition, unknown>>
    if (typeof parsed.sessionId !== "string" || !parsed.sessionId) return null
    if (typeof parsed.queueItemId !== "string" || !parsed.queueItemId) return null
    if (typeof parsed.trackId !== "number" || !Number.isInteger(parsed.trackId)) return null
    if (typeof parsed.seconds !== "number" || !Number.isFinite(parsed.seconds) || parsed.seconds < 0) return null
    return {
      sessionId: parsed.sessionId,
      queueItemId: parsed.queueItemId,
      trackId: parsed.trackId,
      seconds: parsed.seconds,
    }
  } catch {
    return null
  }
}

export function clearPersistedPlaybackPosition() {
  try { localStorage.removeItem(POSITION_KEY) } catch {}
}

export function playbackPositionMatches(
  persisted: PersistedPosition,
  current: Pick<PersistedPosition, "sessionId" | "queueItemId" | "trackId">
): boolean {
  return persisted.sessionId === current.sessionId
    && persisted.queueItemId === current.queueItemId
    && persisted.trackId === current.trackId
}
