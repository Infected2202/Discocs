import type { WaveformLevel, WaveformViewport } from "./types"

export function selectWaveformLevel(
  levels: readonly WaveformLevel[],
  viewport: Pick<WaveformViewport, "width" | "startSeconds" | "endSeconds">,
): WaveformLevel {
  if (levels.length === 0) throw new Error("Waveform timeline must contain at least one level")

  const secondsPerPixel = Math.max(0, viewport.endSeconds - viewport.startSeconds)
    / Math.max(1, viewport.width)
  const ordered = [...levels].sort((left, right) => right.bucketDurationSeconds - left.bucketDurationSeconds)
  const finest = ordered.at(-1)
  if (!finest) throw new Error("Waveform timeline must contain at least one level")
  return ordered.find((level) => level.bucketDurationSeconds <= secondsPerPixel)
    ?? finest
}

export function pointerXToTime(
  pointerX: number,
  viewport: Pick<WaveformViewport, "width" | "startSeconds" | "endSeconds">,
): number {
  const fraction = Math.min(1, Math.max(0, pointerX / Math.max(1, viewport.width)))
  return viewport.startSeconds + fraction * (viewport.endSeconds - viewport.startSeconds)
}

export function resolveFollowWindow(
  playheadSeconds: number,
  visibleSeconds: number,
): Pick<WaveformViewport, "startSeconds" | "endSeconds"> {
  const span = Math.max(0.001, visibleSeconds)
  const startSeconds = playheadSeconds - span / 2
  return { startSeconds, endSeconds: startSeconds + span }
}

export function tapeDragTime(
  initialSeconds: number,
  deltaPixels: number,
  viewport: Pick<WaveformViewport, "width" | "startSeconds" | "endSeconds">,
  durationSeconds: number,
): number {
  const visibleSeconds = viewport.endSeconds - viewport.startSeconds
  const next = initialSeconds - deltaPixels * visibleSeconds / Math.max(1, viewport.width)
  return Math.min(Math.max(0, next), Math.max(0, durationSeconds))
}
