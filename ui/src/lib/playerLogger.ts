function isEnabled(): boolean {
  return import.meta.env.DEV || localStorage.getItem("discocs.debug") === "1"
}

export function playerLog(area: string, msg: string, data?: Record<string, unknown>): void {
  if (!isEnabled()) return
  const args: unknown[] = [`[player:${area}] ${msg}`]
  if (data !== undefined) args.push(data)
  console.debug(...args)
}
