const BACKDROP_ARTWORK_SIZE = 320

export type BackdropAnimationState = "running" | "paused"
export interface BackdropLayerState {
  visibleUrl: string | undefined
  fadingUrl: string | undefined
}

export function artworkBackdropUrl(
  url: string | null | undefined,
  size = BACKDROP_ARTWORK_SIZE
): string | undefined {
  if (!url) return undefined

  const hashIndex = url.indexOf("#")
  const base = hashIndex >= 0 ? url.slice(0, hashIndex) : url
  const hash = hashIndex >= 0 ? url.slice(hashIndex) : ""

  if (/([?&])size=\d+/.test(base)) {
    return `${base.replace(/([?&])size=\d+/, `$1size=${size}`)}${hash}`
  }

  return `${base}${base.includes("?") ? "&" : "?"}size=${size}${hash}`
}

export function backdropAnimationState(
  isPlaying: boolean
): BackdropAnimationState {
  return isPlaying ? "running" : "paused"
}

export function resolveBackdropLayers(
  currentVisibleUrl: string | undefined,
  nextUrl: string | undefined
): BackdropLayerState {
  if (currentVisibleUrl === nextUrl) {
    return {
      visibleUrl: currentVisibleUrl,
      fadingUrl: undefined,
    }
  }

  return {
    visibleUrl: nextUrl,
    fadingUrl: currentVisibleUrl,
  }
}
