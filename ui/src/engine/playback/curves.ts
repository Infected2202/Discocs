import type { DeckId, EqBand } from "./types"

export const DEFAULT_PARAMETER_RAMP_SECONDS = 0.015

export function clamp(value: number, minimum: number, maximum: number): number {
  if (!Number.isFinite(value)) return minimum
  return Math.min(maximum, Math.max(minimum, value))
}

export function clampNormalized(value: number): number {
  return clamp(value, 0, 1)
}

export function clampBipolar(value: number): number {
  return clamp(value, -1, 1)
}

export function equalPowerCrossfader(value: number): Record<DeckId, number> {
  const x = clampBipolar(value)
  const angle = ((x + 1) * Math.PI) / 4
  return { A: Math.cos(angle), B: Math.sin(angle) }
}

export function channelFaderGain(value: number): number {
  const normalized = clampNormalized(value)
  return normalized * normalized
}

export function trimGain(value: number): number {
  const db = -12 + clampNormalized(value) * 24
  return 10 ** (db / 20)
}

export function eqGainDb(band: EqBand, value: number): number {
  const range: Record<EqBand, readonly [number, number]> = {
    low: [-24, 6],
    mid: [-24, 6],
    high: [-24, 6],
  }
  const [minimum, maximum] = range[band]
  return minimum + clampNormalized(value) * (maximum - minimum)
}

export interface FilterFrequencies {
  lowpassHz: number
  highpassHz: number
}

export function filterFrequencies(value: number): FilterFrequencies {
  const normalized = clampBipolar(value)
  if (normalized < 0) {
    return {
      lowpassHz: 20 * (1000 ** (normalized + 1)),
      highpassHz: 20,
    }
  }
  return {
    lowpassHz: 20_000,
    highpassHz: 20 * (1000 ** normalized),
  }
}

export interface RampWindow {
  startTime: number
  endTime: number
}

export function parameterRampWindow(
  currentTime: number,
  requestedTime?: number,
  duration = DEFAULT_PARAMETER_RAMP_SECONDS,
): RampWindow {
  const now = Math.max(0, currentTime)
  const startTime = Math.max(now, requestedTime ?? now)
  return {
    startTime,
    endTime: startTime + Math.max(0, duration),
  }
}
