export const FLOW_FEEDBACK_EVENTS = new Set([
  "completed",
  "skipped",
  "liked",
  "disliked",
])

export interface RefillPlan {
  engine: "flow" | "autoplay"
  sendEvent: boolean
}

/**
 * Decides which engine handles refill for this session and whether
 * a feedback event should be forwarded to /flow/event before refilling.
 */
export function planRefill(
  sourceType: string | undefined,
  eventType?: string,
): RefillPlan {
  const engine = sourceType === "flow" ? "flow" : "autoplay"
  const sendEvent = engine === "flow" && !!eventType && FLOW_FEEDBACK_EVENTS.has(eventType)
  return { engine, sendEvent }
}
