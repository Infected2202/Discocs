import type { WaveformLevel, WaveformViewport } from "./types"

export function selectWaveformLevel(
  levels: readonly WaveformLevel[],
  viewport: Pick<WaveformViewport, "width" | "startSeconds" | "endSeconds">,
): WaveformLevel {
  if (levels.length === 0) throw new Error("Waveform timeline must contain at least one level")

  const secondsPerPixel = Math.max(0, viewport.endSeconds - viewport.startSeconds)
    / Math.max(1, viewport.width)
  const ordered = [...levels].sort((left, right) => right.bucketDurationSeconds - left.bucketDurationSeconds)
  return ordered.find((level) => level.bucketDurationSeconds <= secondsPerPixel)
    ?? ordered[ordered.length - 1]!
}

export function pointerXToTime(
  pointerX: number,
  viewport: Pick<WaveformViewport, "width" | "startSeconds" | "endSeconds">,
): number {
  const fraction = Math.min(1, Math.max(0, pointerX / Math.max(1, viewport.width)))
  return viewport.startSeconds + fraction * (viewport.endSeconds - viewport.startSeconds)
}

export function resolveFollowWindow(
  durationSeconds: number,
  playheadSeconds: number,
  visibleSeconds: number,
): Pick<WaveformViewport, "startSeconds" | "endSeconds"> {
  const span = Math.min(Math.max(0.001, visibleSeconds), durationSeconds)
  const startSeconds = Math.min(
    Math.max(0, playheadSeconds - span / 2),
    Math.max(0, durationSeconds - span),
  )
  return { startSeconds, endSeconds: startSeconds + span }
}
