function isEnabled(): boolean {
  return import.meta.env.DEV || localStorage.getItem("discocs.debug") === "1"
}

export function playerLog(area: string, msg: string, data?: Record<string, unknown>): void {
  if (!isEnabled()) return
  if (data !== undefined) {
    console.debug(`[player:${area}] ${msg}`, data)
  } else {
    console.debug(`[player:${area}] ${msg}`)
  }
}
