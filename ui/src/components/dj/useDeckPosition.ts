import { useCallback, useSyncExternalStore } from "react"
import { playerPlayback, type DeckId, type DeckSnapshot } from "@/engine/playback"

const FRAME_INTERVAL_MS = 1000 / 30
const positions: Record<DeckId, number | null> = { A: null, B: null }
const listeners = new Set<() => void>()
let frame: number | null = null
let previousFrameTime = 0

function readPositions(): void {
  let changed = false
  for (const deck of ["A", "B"] as const) {
    const next = playerPlayback.getDeckCurrentTime(deck)
    if (next !== null && next !== positions[deck]) {
      positions[deck] = next
      changed = true
    }
  }
  if (changed) listeners.forEach((listener) => listener())
}

function tick(time: number): void {
  if (time - previousFrameTime >= FRAME_INTERVAL_MS) {
    previousFrameTime = time
    readPositions()
  }
  frame = listeners.size > 0 ? requestAnimationFrame(tick) : null
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  if (listeners.size === 1) {
    readPositions()
    frame = requestAnimationFrame(tick)
  }
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0 && frame !== null) {
      cancelAnimationFrame(frame)
      frame = null
      previousFrameTime = 0
    }
  }
}

export function useDeckPosition(deck: DeckSnapshot): number {
  const fallback = deck.anchor?.mediaSeconds ?? 0
  const playing = deck.transport === "playing"
  const subscribeToClock = useCallback(
    (listener: () => void) => playing ? subscribe(listener) : () => undefined,
    [playing],
  )
  const getSnapshot = useCallback(
    () => playing ? positions[deck.id] ?? fallback : fallback,
    [deck.id, fallback, playing],
  )
  return useSyncExternalStore(subscribeToClock, getSnapshot, () => fallback)
}
