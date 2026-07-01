export type PlasmaRgb = [number, number, number]

export const plasmaShaderInitializers = `
  float i = 0.0;
  float d = 0.0;
  float z = 0.0;
  float T = iTime * uSpeed;
  vec3 O = vec3(0.0);
  vec3 p = vec3(0.0);
  vec3 S = vec3(0.0);
`

export function keepPlasmaMountedBetweenTracks(
  currentlyReady: boolean,
  nextAccentReady: boolean
): boolean {
  return currentlyReady || nextAccentReady
}

const FALLBACK_COLOR: PlasmaRgb = [1, 0.165, 0.427]
const TRACK_ACCENT_TRANSITION_FALLBACK_MS = 320

export function parsePlasmaColor(value: string): PlasmaRgb {
  const color = value.trim()
  const hex = /^#?([\da-f]{2})([\da-f]{2})([\da-f]{2})$/i.exec(color)
  if (hex) {
    return [
      Number.parseInt(hex[1], 16) / 255,
      Number.parseInt(hex[2], 16) / 255,
      Number.parseInt(hex[3], 16) / 255,
    ]
  }

  const rgb =
    /^rgba?\(\s*(\d+(?:\.\d+)?)\s*[, ]\s*(\d+(?:\.\d+)?)\s*[, ]\s*(\d+(?:\.\d+)?)/i.exec(
      color
    )
  if (rgb) {
    return [
      Math.min(255, Number(rgb[1])) / 255,
      Math.min(255, Number(rgb[2])) / 255,
      Math.min(255, Number(rgb[3])) / 255,
    ]
  }

  return FALLBACK_COLOR
}

export function readTrackAccentColor(): PlasmaRgb {
  return parsePlasmaColor(
    getComputedStyle(document.documentElement).getPropertyValue("--track-accent")
  )
}

export function mixPlasmaColor(
  from: PlasmaRgb,
  to: PlasmaRgb,
  progress: number,
): PlasmaRgb {
  const clamped = Math.max(0, Math.min(1, progress))
  return [
    from[0] + (to[0] - from[0]) * clamped,
    from[1] + (to[1] - from[1]) * clamped,
    from[2] + (to[2] - from[2]) * clamped,
  ]
}

function cubicBezierSample(a: number, b: number, c: number, t: number): number {
  const inverse = 1 - t
  return 3 * inverse * inverse * t * a + 3 * inverse * t * t * b + t * t * t * c
}

function cubicBezierDerivative(a: number, b: number, c: number, t: number): number {
  const inverse = 1 - t
  return (
    3 * inverse * inverse * a
    + 6 * inverse * t * (b - a)
    + 3 * t * t * (c - b)
  )
}

export function easeTrackAccentTransition(progress: number): number {
  const clamped = Math.max(0, Math.min(1, progress))
  if (clamped === 0 || clamped === 1) return clamped

  let t = clamped
  for (let index = 0; index < 5; index += 1) {
    const x = cubicBezierSample(0.25, 0.25, 1, t) - clamped
    const slope = cubicBezierDerivative(0.25, 0.25, 1, t)
    if (Math.abs(slope) < 1e-6) break
    t = Math.max(0, Math.min(1, t - x / slope))
  }

  return cubicBezierSample(0.1, 1, 1, t)
}

export function readTrackAccentTransitionDurationMs(): number {
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue("--track-accent-transition-duration")
    .trim()

  if (!raw) return TRACK_ACCENT_TRANSITION_FALLBACK_MS
  if (raw.endsWith("ms")) {
    const value = Number.parseFloat(raw)
    return Number.isFinite(value) ? value : TRACK_ACCENT_TRANSITION_FALLBACK_MS
  }
  if (raw.endsWith("s")) {
    const value = Number.parseFloat(raw)
    return Number.isFinite(value)
      ? value * 1000
      : TRACK_ACCENT_TRANSITION_FALLBACK_MS
  }
  return TRACK_ACCENT_TRANSITION_FALLBACK_MS
}
