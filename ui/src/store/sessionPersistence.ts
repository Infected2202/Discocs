const SESSION_KEY = "discocs.sessionId.v1"

export function persistSessionId(sessionId: string) {
  try { localStorage.setItem(SESSION_KEY, sessionId) } catch {}
}

export function loadPersistedSessionId(): string | null {
  try { return localStorage.getItem(SESSION_KEY) } catch { return null }
}

export function clearPersistedSessionId() {
  try { localStorage.removeItem(SESSION_KEY) } catch {}
}
