import { useCallback, useSyncExternalStore } from "react"
import { playerPlayback, type DeckId } from "@/engine/playback"

type MeterId = DeckId | "master"

const FRAME_INTERVAL_MS = 50
let levels: Record<MeterId, number> = { A: 0, B: 0, master: 0 }
const listeners = new Set<() => void>()
let frame: number | null = null
let previousFrameTime = 0

function readMeters(): void {
  const next = playerPlayback.getMixerMeters()
  if (
    next.A === levels.A
    && next.B === levels.B
    && next.master === levels.master
  ) return
  levels = next
  listeners.forEach((listener) => listener())
}

function tick(time: number): void {
  if (time - previousFrameTime >= FRAME_INTERVAL_MS) {
    previousFrameTime = time
    readMeters()
  }
  frame = listeners.size > 0 ? requestAnimationFrame(tick) : null
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  if (listeners.size === 1) {
    readMeters()
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

export function useMixerMeter(meter: MeterId): number {
  const getSnapshot = useCallback(() => levels[meter], [meter])
  return useSyncExternalStore(subscribe, getSnapshot, () => 0)
}
