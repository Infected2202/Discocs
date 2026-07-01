import { getPaletteSync } from "colorthief"

import { playerLog } from "@/lib/playerLogger"

const DEFAULT_ACCENT = "#ff2a6d"
const DEFAULT_FOREGROUND = "#07110e"
const BACKGROUND_RGB: Rgb = { r: 11 / 255, g: 13 / 255, b: 15 / 255 }
const SAMPLE_SIZE = 96
const CACHE_LIMIT = 64
const PALETTE_SIZE = 8

interface Rgb {
  r: number
  g: number
  b: number
}

interface Oklch {
  l: number
  c: number
  h: number
}

interface PaletteCandidate {
  accent: Rgb
  oklch: Oklch
  proportion: number
  population: number
}

export interface ArtworkPalette {
  accent: string
  foreground: string
}

export const DEFAULT_ARTWORK_PALETTE: ArtworkPalette = {
  accent: DEFAULT_ACCENT,
  foreground: DEFAULT_FOREGROUND,
}

const paletteCache = new Map<string, ArtworkPalette>()

function srgbToLinear(value: number): number {
  return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
}

function linearToSrgb(value: number): number {
  return value <= 0.0031308 ? value * 12.92 : 1.055 * value ** (1 / 2.4) - 0.055
}

function oklchToRgb(l: number, c: number, h: number): Rgb {
  const a = Math.cos(h) * c
  const b = Math.sin(h) * c
  const ll = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3
  const mm = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3
  const ss = (l - 0.0894841775 * a - 1.291485548 * b) ** 3

  return {
    r: linearToSrgb(4.0767416621 * ll - 3.3077115913 * mm + 0.2309699292 * ss),
    g: linearToSrgb(-1.2684380046 * ll + 2.6097574011 * mm - 0.3413193965 * ss),
    b: linearToSrgb(-0.0041960863 * ll - 0.7034186147 * mm + 1.707614701 * ss),
  }
}

function relativeLuminance(rgb: Rgb): number {
  return (
    0.2126 * srgbToLinear(rgb.r)
    + 0.7152 * srgbToLinear(rgb.g)
    + 0.0722 * srgbToLinear(rgb.b)
  )
}

function contrastRatio(left: Rgb, right: Rgb): number {
  const lighter = Math.max(relativeLuminance(left), relativeLuminance(right))
  const darker = Math.min(relativeLuminance(left), relativeLuminance(right))
  return (lighter + 0.05) / (darker + 0.05)
}

function inSrgbGamut(rgb: Rgb): boolean {
  return rgb.r >= 0 && rgb.r <= 1 && rgb.g >= 0 && rgb.g <= 1 && rgb.b >= 0 && rgb.b <= 1
}

function gamutMappedRgb(l: number, requestedChroma: number, hue: number): Rgb {
  let low = 0
  let high = requestedChroma
  let result = oklchToRgb(l, 0, hue)
  for (let index = 0; index < 12; index += 1) {
    const candidateChroma = (low + high) / 2
    const candidate = oklchToRgb(l, candidateChroma, hue)
    if (inSrgbGamut(candidate)) {
      low = candidateChroma
      result = candidate
    } else {
      high = candidateChroma
    }
  }
  return result
}

function toCssRgb(rgb: Rgb): string {
  const channel = (value: number) => Math.round(Math.max(0, Math.min(1, value)) * 255)
  return `rgb(${channel(rgb.r)} ${channel(rgb.g)} ${channel(rgb.b)})`
}

function normalizeHueDegrees(hue: number): number {
  const normalized = hue % 360
  return normalized < 0 ? normalized + 360 : normalized
}

function toCandidate(color: {
  rgb(): { r: number; g: number; b: number }
  oklch(): { l: number; c: number; h: number }
  population: number
  proportion: number
}): PaletteCandidate {
  const rgb = color.rgb()
  const oklch = color.oklch()
  return {
    accent: { r: rgb.r / 255, g: rgb.g / 255, b: rgb.b / 255 },
    oklch: {
      l: oklch.l,
      c: oklch.c,
      h: Number.isFinite(oklch.h) ? normalizeHueDegrees(oklch.h) * (Math.PI / 180) : 0,
    },
    proportion: color.proportion,
    population: color.population,
  }
}

function scoreChromaticCandidate(candidate: PaletteCandidate): number {
  const lightnessFit = 1 - Math.min(0.45, Math.abs(candidate.oklch.l - 0.56))
  const areaScore = Math.min(1, candidate.proportion / 0.22)
  const chromaScore = Math.min(1, candidate.oklch.c / 0.18)
  return areaScore * 0.62 + chromaScore * 0.28 + lightnessFit * 0.1
}

function scoreAccentCandidate(candidate: PaletteCandidate): number {
  const areaScore = Math.min(1, candidate.proportion / 0.08)
  const chromaScore = Math.min(1, candidate.oklch.c / 0.24)
  return chromaScore * 0.7 + areaScore * 0.3
}

function pickCandidate(candidates: PaletteCandidate[]): PaletteCandidate | null {
  if (candidates.length === 0) return null

  const chromatic = candidates
    .filter((candidate) => candidate.oklch.c >= 0.05 && candidate.proportion >= 0.08)
    .sort((left, right) => scoreChromaticCandidate(right) - scoreChromaticCandidate(left))
  if (chromatic.length > 0) return chromatic[0]

  const vividAccent = candidates
    .filter((candidate) => candidate.oklch.c >= 0.09 && candidate.proportion >= 0.02)
    .sort((left, right) => scoreAccentCandidate(right) - scoreAccentCandidate(left))
  if (vividAccent.length > 0) return vividAccent[0]

  return [...candidates].sort((left, right) => right.proportion - left.proportion)[0]
}

function finalizeAccent(candidate: PaletteCandidate): ArtworkPalette {
  const isChromatic = candidate.oklch.c >= 0.03
  let accent: Rgb

  if (isChromatic) {
    const targetChroma = Math.min(Math.max(candidate.oklch.c, 0.09), 0.22)
    let targetLightness = Math.min(Math.max(candidate.oklch.l, 0.58), 0.78)
    accent = gamutMappedRgb(targetLightness, targetChroma, candidate.oklch.h)
    while (contrastRatio(accent, BACKGROUND_RGB) < 4.5 && targetLightness < 0.84) {
      targetLightness += 0.01
      accent = gamutMappedRgb(targetLightness, targetChroma, candidate.oklch.h)
    }
  } else {
    let targetLightness = Math.min(Math.max(candidate.oklch.l, 0.52), 0.82)
    accent = gamutMappedRgb(targetLightness, candidate.oklch.c, candidate.oklch.h)
    while (contrastRatio(accent, BACKGROUND_RGB) < 4.5 && targetLightness < 0.88) {
      targetLightness += 0.01
      accent = gamutMappedRgb(targetLightness, candidate.oklch.c, candidate.oklch.h)
    }
  }

  const darkForeground: Rgb = { r: 7 / 255, g: 17 / 255, b: 14 / 255 }
  const lightForeground: Rgb = { r: 238 / 255, g: 242 / 255, b: 243 / 255 }
  const foreground = contrastRatio(accent, darkForeground) >= 4.5
    ? DEFAULT_FOREGROUND
    : toCssRgb(lightForeground)

  return { accent: toCssRgb(accent), foreground }
}

function paletteFromBitmap(bitmap: ImageBitmap): ArtworkPalette {
  const palette = getPaletteSync(bitmap, {
    colorCount: PALETTE_SIZE,
    colorSpace: "oklch",
    ignoreWhite: false,
    quality: 1,
  })

  if (!palette || palette.length === 0) {
    playerLog("palette", "no palette - fallback to default")
    return DEFAULT_ARTWORK_PALETTE
  }

  const candidates = palette.map(toCandidate)
  const selected = pickCandidate(candidates)
  if (!selected) {
    playerLog("palette", "no candidate - fallback to default")
    return DEFAULT_ARTWORK_PALETTE
  }

  const result = finalizeAccent(selected)
  playerLog("palette", "extracted", {
    accent: result.accent,
    foreground: result.foreground,
    candidateChroma: selected.oklch.c,
    candidateLightness: selected.oklch.l,
    candidateProportion: selected.proportion,
    population: selected.population,
    palette: candidates.map((candidate) => ({
      chroma: candidate.oklch.c,
      lightness: candidate.oklch.l,
      proportion: candidate.proportion,
      rgb: toCssRgb(candidate.accent),
    })),
  })
  return result
}

function rememberPalette(url: string, palette: ArtworkPalette) {
  paletteCache.delete(url)
  paletteCache.set(url, palette)
  if (paletteCache.size > CACHE_LIMIT) {
    const oldest = paletteCache.keys().next().value
    if (typeof oldest === "string") paletteCache.delete(oldest)
  }
}

export async function extractArtworkPalette(
  url: string,
  signal?: AbortSignal,
): Promise<ArtworkPalette> {
  const cached = paletteCache.get(url)
  if (cached) {
    playerLog("palette", "cache hit", { url })
    rememberPalette(url, cached)
    return cached
  }

  playerLog("palette", "fetching artwork", { url })
  const response = await fetch(url, { signal })
  if (!response.ok) throw new Error(`Artwork request failed: ${response.status}`)
  const bitmap = await createImageBitmap(await response.blob(), {
    resizeWidth: SAMPLE_SIZE,
    resizeHeight: SAMPLE_SIZE,
    resizeQuality: "high",
  })

  try {
    const palette = paletteFromBitmap(bitmap)
    rememberPalette(url, palette)
    return palette
  } finally {
    bitmap.close()
  }
}
